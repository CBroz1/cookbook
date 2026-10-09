#!/usr/bin/env python3
"""Shared tooling for generating and verifying cookbook recipes.

Both the generate and verify protocols run the same stages; only the
middle step differs.

    generate:  fetch/parse -> normalize -> draft  -> lint -> fmt -> log
    verify:    fetch/parse -> normalize -> compare -> lint -> fmt -> log

Every stage is a subcommand here, so the two protocols share one
implementation. Stages exchange a "source record" JSON on disk, which
means a blocked HTTP fetch can be replaced by a saved page without
changing anything downstream.

Standard library only, plus mdformat for the fmt stage.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------
# House conventions, mirroring template.md
# --------------------------------------------------------------------

SECTIONS = ["Shopping", "Prep", "Cook", "Notes", "Citation"]
CATEGORIES = ["Produce", "Dairy", "Middle", "Staples"]
NOTE_LABELS = [
    "Suggested Time",
    "Prep",
    "Cook",
    "Rest",
    "Servings",
    "Yield",
    "Substitutions",
]
MAX_LINE = 80

# Spelled-out units -> house abbreviation. Order matters: longest first.
UNIT_WORDS = [
    (r"tablespoons?", "tbsp"),
    (r"teaspoons?", "tsp"),
    (r"milliliters?", "ml"),
    (r"millilitres?", "ml"),
    (r"kilograms?", "kg"),
    (r"grams?", "g"),
    (r"pounds?", "lb"),
    (r"ounces?", "oz"),
    (r"minutes?", "m"),
    (r"mins?", "m"),
    (r"seconds?", "s"),
    (r"secs?", "s"),
    (r"hours?", "h"),
    (r"hrs?", "h"),
    (r"cups?", "c"),
]
ABBREVS = ["tbsp", "tsp", "oz", "lb", "ml", "kg", "g", "c"]

# A quantity token: 2, 1.5 or .5. The lookbehind keeps it from biting
# into a longer word or a decimal that is already matched.
NUM = r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)"

VULGAR = {
    "½": ".5",
    "⅓": ".33",
    "⅔": ".67",
    "¼": ".25",
    "¾": ".75",
    "⅛": ".125",
    "⅜": ".375",
    "⅝": ".625",
    "⅞": ".875",
}
ASCII_FRACTIONS = {
    "1/2": ".5",
    "1/3": ".33",
    "2/3": ".67",
    "1/4": ".25",
    "3/4": ".75",
    "1/8": ".125",
    "3/8": ".375",
    "5/8": ".625",
    "7/8": ".875",
}

DATE_FMT = "%Y-%m-%d"
STATUSES = ["pending", "verified", "updated", "no-source", "blocked"]

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# --------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------


@dataclass
class Finding:
    level: str  # "error" | "warn"
    path: str
    line: int
    code: str
    msg: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.level[0].upper()} {self.code} {self.msg}"


# --------------------------------------------------------------------
# Stage 1: fetch / parse -> source record
# --------------------------------------------------------------------


def _iter_recipes(obj):
    """Yield every schema.org Recipe node in a decoded JSON-LD blob."""
    if isinstance(obj, dict):
        t = obj.get("@type")
        if t == "Recipe" or (isinstance(t, list) and "Recipe" in t):
            yield obj
        for v in obj.values():
            yield from _iter_recipes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_recipes(v)


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _flatten_instructions(raw) -> list[str]:
    out: list[str] = []
    if isinstance(raw, str):
        parts = re.split(r"(?:\r?\n)+|(?<=\.)\s{2,}", raw)
        out.extend(_strip_tags(p) for p in parts)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.append(_strip_tags(item))
            elif isinstance(item, dict):
                if item.get("@type") == "HowToSection":
                    name = _strip_tags(str(item.get("name", "")))
                    if name:
                        out.append(f"## {name}")
                    out.extend(_flatten_instructions(item.get("itemListElement")))
                else:
                    txt = item.get("text") or item.get("name") or ""
                    out.append(_strip_tags(str(txt)))
    return [s for s in out if s]


def _iso_to_minutes(value) -> int | None:
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", value.strip())
    if not m:
        return None
    days, hours, mins = (int(g or 0) for g in m.groups())
    total = days * 1440 + hours * 60 + mins
    return total or None


def extract_source(page_html: str, url: str) -> dict:
    """Build a source record from page HTML, preferring JSON-LD."""
    for blob in re.findall(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
        page_html,
        re.S | re.I,
    ):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for node in _iter_recipes(data):
            y = node.get("recipeYield")
            if isinstance(y, list):
                y = ", ".join(str(i) for i in y)
            return {
                "url": url,
                "extracted_via": "json-ld",
                "name": _strip_tags(str(node.get("name") or "")),
                "ingredients": [
                    _strip_tags(str(i))
                    for i in (node.get("recipeIngredient") or [])
                ],
                "instructions": _flatten_instructions(
                    node.get("recipeInstructions")
                ),
                "prep_minutes": _iso_to_minutes(node.get("prepTime")),
                "cook_minutes": _iso_to_minutes(node.get("cookTime")),
                "total_minutes": _iso_to_minutes(node.get("totalTime")),
                "yield": str(y) if y else None,
            }

    title = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.S | re.I)
    return {
        "url": url,
        "extracted_via": "none",
        "name": _strip_tags(title.group(1)) if title else "",
        "ingredients": [],
        "instructions": [],
        "prep_minutes": None,
        "cook_minutes": None,
        "total_minutes": None,
        "yield": None,
        "note": (
            "No JSON-LD Recipe found. Re-run the fetch stage with the "
            "WebFetch fallback and feed the result to `parse`."
        ),
    }


STUB_MARKER = "--- PASTE PAGE TEXT BELOW THIS LINE ---"

_ING_HEAD = re.compile(
    r"^\s*(ingredients?|what you.?ll need)\s*:?\s*$", re.I
)
_INST_HEAD = re.compile(
    r"^\s*(directions?|instructions?|method|preparation|steps|"
    r"how to make[^:]*)\s*:?\s*$",
    re.I,
)
_END_HEAD = re.compile(
    r"^\s*(notes?|nutrition.*|special equipment|equipment|make.?ahead.*|"
    r"storage|comments?|reviews?|related.*|you might also like|"
    r"more recipes.*|leave a (reply|comment)|rate this recipe|"
    r"did you make this.*)\s*:?\s*$",
    re.I,
)
# Chrome and site furniture that survives a copy/paste.
_JUNK = re.compile(
    r"^\s*(save|rate|print|share|pin|jump to .*|view all|get the app|"
    r"keep screen awake|advertisement|close|menu|search|sign up|log in|"
    r"subscribe|follow|photo|image credit)\s*:?\s*$",
    re.I,
)
_QTY_HINT = re.compile(
    r"(^\s*[\d\u00bc-\u00be\u2150-\u215e]|"
    r"\b(cups?|tablespoons?|teaspoons?|ounces?|pounds?|cloves?|cans?|"
    r"packages?|pinch|dash|tsp|tbsp|oz|lbs?|ml|g|kg|sprigs?|bunch|"
    r"handful|to taste)\b)",
    re.I,
)
_TIME_LABEL = re.compile(
    r"^\s*(prep|cook|total|active|rest|chill|bake|inactive)\s*time\s*:?\s*(.*)$",
    re.I,
)
_YIELD_LABEL = re.compile(r"^\s*(yield|servings?|makes)\s*:?\s*(.*)$", re.I)


def _dur_minutes(text: str) -> int | None:
    """Parse '58 mins', '1 hr 10 mins', '2 hours' into minutes."""
    if not text:
        return None
    total = 0
    for num, unit in re.findall(
        r"(\d+(?:\.\d+)?)\s*(h(?:ou)?rs?|hr|mins?|minutes?|m\b)", text, re.I
    ):
        n = float(num)
        total += int(n * 60) if unit.lower().startswith("h") else int(n)
    return total or None


def _looks_like_ingredient(line: str) -> bool:
    if len(line) > 160 or line.endswith("."):
        return False
    return bool(_QTY_HINT.search(line))


def extract_pasted_text(body: str, url: str = "") -> dict:
    """Pull a recipe out of page text copied from a browser.

    Copy/paste carries nav menus, a story, photo credits and comments.
    The recipe card is found by scoring every 'Ingredients' heading on
    how many quantity-bearing lines follow it, so a nav entry with the
    same label does not win.
    """
    raw = [ln.strip() for ln in body.splitlines()]
    # Lines repeated three or more times are furniture or photo credits.
    counts: dict[str, int] = {}
    for ln in raw:
        if ln:
            counts[ln] = counts.get(ln, 0) + 1
    def _is_head(ln: str) -> bool:
        return bool(
            _ING_HEAD.match(ln) or _INST_HEAD.match(ln) or _END_HEAD.match(ln)
        )

    # Headings are exempt: nav menus repeat "Ingredients" many times, and
    # dropping it would take the real card's heading with it.
    lines = [
        ln
        for ln in raw
        if ln
        and not _JUNK.match(ln)
        and (_is_head(ln) or counts.get(ln, 0) < 3)
    ]

    ing_heads = [i for i, ln in enumerate(lines) if _ING_HEAD.match(ln)]
    inst_heads = [i for i, ln in enumerate(lines) if _INST_HEAD.match(ln)]

    # The card is the ingredient heading NEAREST ABOVE an instruction
    # heading. Nav menus repeat "Ingredients" far from the card, and
    # their blocks would otherwise swallow the ratings and the story and
    # outscore the real list.
    best = None
    for j in inst_heads:
        above = [i for i in ing_heads if i < j]
        if not above:
            continue
        i = max(above)
        if j - i > 60:  # too far apart to be one card
            continue
        block = lines[i + 1 : j]
        score = sum(1 for ln in block if _looks_like_ingredient(ln))
        if score >= 2 and (best is None or score > best[0]):
            best = (score, i, j)

    if best is None:  # no instruction heading; take the densest block
        for i in ing_heads:
            stop = min(i + 40, len(lines))
            block = lines[i + 1 : stop]
            score = sum(1 for ln in block if _looks_like_ingredient(ln))
            if score >= 3 and (best is None or score > best[0]):
                best = (score, i, None)

    ingredients: list[str] = []
    instructions: list[str] = []
    if best:
        _score, i, j = best
        stop = j if j is not None else min(i + 60, len(lines))
        for ln in lines[i + 1 : stop]:
            if _END_HEAD.match(ln):
                break
            if ln.endswith(":"):  # "For Fritters:" style subheading
                continue
            # Inside the bounded card, a short line without a quantity is
            # still an ingredient ("Freshly ground black pepper").
            if _looks_like_ingredient(ln) or len(ln.split()) <= 8:
                ingredients.append(ln)
        if j is not None:
            for ln in lines[j + 1 :]:
                if _END_HEAD.match(ln) or _ING_HEAD.match(ln):
                    break
                if len(ln) > 25 or re.match(r"^\s*\d+[.)]", ln):
                    instructions.append(re.sub(r"^\s*\d+[.)]\s*", "", ln))

    # Times and yield: same line, or label then value on the next line.
    times: dict[str, int] = {}
    yields: list[str] = []
    for idx, ln in enumerate(lines):
        m = _TIME_LABEL.match(ln)
        if m:
            val = m.group(2).strip() or (
                lines[idx + 1] if idx + 1 < len(lines) else ""
            )
            mins = _dur_minutes(val)
            if mins:
                times.setdefault(m.group(1).lower(), mins)
            continue
        m = _YIELD_LABEL.match(ln)
        if m:
            val = m.group(2).strip() or (
                lines[idx + 1] if idx + 1 < len(lines) else ""
            )
            if val and not _TIME_LABEL.match(val) and len(val) < 60:
                yields.append(val)

    rec = {
        "url": url,
        "extracted_via": "pasted-text",
        "name": "",
        "ingredients": ingredients,
        "instructions": instructions,
        "prep_minutes": times.get("prep"),
        "cook_minutes": times.get("cook") or times.get("bake"),
        "total_minutes": times.get("total"),
        "yield": yields[0] if yields else None,
    }
    if not ingredients:
        rec["note"] = (
            "Could not locate an ingredient block. Paste the recipe card "
            "itself, or transcribe by hand."
        )
    return rec


def read_stub(path: Path) -> tuple[dict, str]:
    """Split a staged paste file into its headers and pasted body."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if STUB_MARKER not in text:
        return {}, text
    head, _, body = text.partition(STUB_MARKER)
    headers = {}
    for ln in head.splitlines():
        m = re.match(r"^\s*([A-Z]+)\s*:\s*(.*)$", ln)
        if m:
            headers[m.group(1).lower()] = m.group(2).strip()
    return headers, body


def http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


# --------------------------------------------------------------------
# Stage 2: normalize (the replace_text.sh replacement)
# --------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Apply house quantity and unit rules to free text."""
    out = text

    # Vulgar fractions, including the "1 1/2" mixed-number case.
    for glyph, dec in VULGAR.items():
        out = re.sub(r"(\d)\s*" + re.escape(glyph), lambda m, d=dec: _mix(m.group(1), d), out)
        out = out.replace(glyph, dec)
    for frac, dec in ASCII_FRACTIONS.items():
        pat = r"(?<![\d/])(\d+)\s+" + re.escape(frac) + r"(?![\d/])"
        out = re.sub(pat, lambda m, d=dec: _mix(m.group(1), d), out)
        out = re.sub(r"(?<![\d/])" + re.escape(frac) + r"(?![\d/])", dec, out)

    # Spelled-out units -> abbreviation, closed up against the number.
    for word, abbr in UNIT_WORDS:
        out = re.sub(
            NUM + r"\s*" + word + r"(?![\w])",
            lambda m, a=abbr: f"{m.group(1)}{a}",
            out,
            flags=re.I,
        )

    # Close the gap on already-abbreviated units.
    alts = "|".join(ABBREVS)
    out = re.sub(
        NUM + r"\s+(" + alts + r")(?![\w])",
        r"\1\2",
        out,
    )

    # "2tbsps" / "2cs" -> "2tbsp" / "2c". The unit never takes a plural.
    out = re.sub(
        NUM + r"(tbsp|tsp|oz|lb|ml|kg|g|c)s(?![\w])",
        r"\1\2",
        out,
    )
    return out


def _mix(whole: str, dec: str) -> str:
    """Fold a mixed number such as 1 + .5 into 1.5."""
    return f"{int(whole) + float(dec):g}"


# --------------------------------------------------------------------
# Recipe markdown model
# --------------------------------------------------------------------


@dataclass
class Recipe:
    path: Path
    lines: list[str]
    sections: dict[str, tuple[int, int]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Recipe":
        lines = path.read_text(encoding="utf-8").splitlines()
        r = cls(path=path, lines=lines)
        r._index_sections()
        return r

    def _index_sections(self) -> None:
        heads = [
            (i, m.group(1).strip())
            for i, ln in enumerate(self.lines)
            if (m := re.match(r"^##\s+(.*)$", ln))
        ]
        for n, (i, name) in enumerate(heads):
            end = heads[n + 1][0] if n + 1 < len(heads) else len(self.lines)
            self.sections[name] = (i, end)

    def section_lines(self, name: str) -> list[tuple[int, str]]:
        if name not in self.sections:
            return []
        start, end = self.sections[name]
        return [(i + 1, self.lines[i]) for i in range(start + 1, end)]

    def shopping_items(self) -> list[tuple[int, str, str]]:
        """Return (lineno, category, item) for each Shopping entry."""
        out, current = [], None
        for lineno, ln in self.section_lines(self._shopping_name()):
            m = re.match(r"^-\s+(\w+):?\s*(\(none\))?\s*$", ln)
            if m and m.group(1) in CATEGORIES:
                current = m.group(1)
                continue
            m = re.match(r"^\s{4}-\s+(.*\S)\s*$", ln)
            if m and current:
                out.append((lineno, current, m.group(1)))
        return out

    def _shopping_name(self) -> str:
        for alias in ("Shopping", "Ingredients"):
            if alias in self.sections:
                return alias
        return "Shopping"

    def steps_text(self) -> str:
        chunks = []
        for alias in ("Prep", "Preparation", "Cook", "Instructions"):
            chunks += [ln for _, ln in self.section_lines(alias)]
        return "\n".join(chunks)


# --------------------------------------------------------------------
# Stage 4: lint
# --------------------------------------------------------------------


def _callout_body_lines(lines: list[str]) -> set[int]:
    """1-based line numbers that belong to an admonition body."""
    body, inside = set(), False
    for i, ln in enumerate(lines, start=1):
        if re.match(r"^(\?{3}|!{3})[+ ]?\s*\w+", ln):
            inside = True
            continue
        if inside:
            if ln.strip() == "":
                continue
            if ln.startswith("    "):
                body.add(i)
                continue
            inside = False
    return body


def _citation_lines(rec: Recipe) -> set[int]:
    return {n for n, _ in rec.section_lines("Citation")}


def _parse_minutes(text: str) -> int | None:
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", text.strip())
    if not m or not any(m.groups()):
        return None
    h, mi = (int(g or 0) for g in m.groups())
    return h * 60 + mi


def lint_recipe(path: Path) -> list[Finding]:
    f: list[Finding] = []
    add = lambda lv, ln, code, msg: f.append(
        Finding(lv, str(path), ln, code, msg)
    )

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        add("error", 0, "E001", "file is empty")
        return f

    rec = Recipe.load(path)
    lines = rec.lines

    # E002 section names and order
    present = [h for h in rec.sections if h in SECTIONS or h in
               ("Ingredients", "Instructions", "Preparation")]
    canon = {"Ingredients": "Shopping", "Instructions": "Cook",
             "Preparation": "Prep"}
    for name in present:
        if name in canon:
            ln = rec.sections[name][0] + 1
            add("error", ln, "E002",
                f"section '{name}' should be '{canon[name]}'")
    order = [canon.get(h, h) for h in rec.sections if canon.get(h, h) in SECTIONS]
    if order != SECTIONS:
        ln = (rec.sections[next(iter(rec.sections))][0] + 1) if rec.sections else 1
        add("error", ln, "E002",
            f"sections are {order}, expected {SECTIONS}")

    # E003 Shopping categories
    sname = rec._shopping_name()
    seen: list[str] = []
    for lineno, ln in rec.section_lines(sname):
        m = re.match(r"^-\s+(\w+)(:)?\s*(\(none\))?\s*$", ln)
        if not m:
            continue
        cat, colon, none = m.group(1), m.group(2), m.group(3)
        if cat not in CATEGORIES:
            continue
        seen.append(cat)
        if colon:
            add("error", lineno, "E003",
                f"category '{cat}' has a trailing colon")
        if none:
            add("error", lineno, "E003",
                f"category '{cat}' uses '(none)'; leave it bare")
    if seen != CATEGORIES:
        ln = rec.sections.get(sname, (0, 0))[0] + 1
        add("error", ln, "E003",
            f"categories are {seen}, expected {CATEGORIES}")

    # E004 ordered-list marker, E005 interrupted list
    in_ordered = {}
    for name in ("Prep", "Preparation", "Cook", "Instructions"):
        if name not in rec.sections:
            continue
        saw_ordered = False
        for lineno, ln in rec.section_lines(name):
            m = re.match(r"^(\s*)(\d+)\.\s", ln)
            if m:
                if m.group(2) != "1":
                    add("error", lineno, "E004",
                        f"ordered item uses '{m.group(2)}.'; use '1.'")
                if len(m.group(1)) == 0:
                    saw_ordered = True
                continue
            if saw_ordered and re.match(r"^-\s+\S", ln):
                add("error", lineno, "E005",
                    "top-level '-' breaks the ordered list; indent it "
                    "4 spaces under its step")
        in_ordered[name] = saw_ordered

    callout_bodies = _callout_body_lines(lines)
    citation = _citation_lines(rec)

    for i, ln in enumerate(lines, start=1):
        # E006 fractions
        if re.search(r"(?<![\w/])\d+/\d+(?![\w/])", ln) and "http" not in ln:
            add("error", i, "E006", "fraction; use a decimal")
        if any(g in ln for g in VULGAR):
            add("error", i, "E006", "vulgar fraction; use a decimal")

        # E007 spaced or spelled-out units
        alts = "|".join(ABBREVS)
        if re.search(NUM + r"\s+(?:" + alts + r")(?![\w])", ln):
            add("error", i, "E007", "space between number and unit")
        for word, abbr in UNIT_WORDS:
            if re.search(NUM + r"\s*" + word + r"(?![\w])", ln, re.I):
                add("error", i, "E007",
                    f"spelled-out unit; use '{abbr}'")
                break

        # E013 pluralised unit abbreviation
        if re.search(NUM + r"(?:tbsp|tsp|oz|lb|ml|kg|c)s(?![\w])", ln):
            add("error", i, "E013", "unit abbreviation is pluralised")

        # E008 placeholders
        if re.search(r":\s*X\s*$", ln) or re.search(r"\d+X", ln):
            add("error", i, "E008", "unfilled placeholder 'X'")

        # E009 long lines
        if (
            len(ln) > MAX_LINE
            and i not in callout_bodies
            and i not in citation
            and not ln.lstrip().startswith("|")
        ):
            add("error", i, "E009", f"line is {len(ln)} chars (max {MAX_LINE})")

        # E010 legacy NOTE styles
        if re.match(r"^\s*(_{1,2}|\*{1,2})?NOTE", ln):
            add("error", i, "E010",
                "legacy NOTE; use a '!!! note' callout")

    # E011 Notes labels and W003 time arithmetic
    times: dict[str, int] = {}
    if "Notes" in rec.sections:
        labels = []
        for lineno, ln in rec.section_lines("Notes"):
            m = re.match(r"^\s*-\s+([A-Za-z][A-Za-z ]*?):\s*(.*)$", ln)
            if not m:
                continue
            label, value = m.group(1).strip(), m.group(2).strip()
            labels.append(label)
            if label not in NOTE_LABELS:
                add("error", lineno, "E011",
                    f"unknown Notes label '{label}'")
            mins = _parse_minutes(value)
            if mins is not None:
                times[label] = mins
        for required in ("Suggested Time",):
            if required not in labels:
                ln = rec.sections["Notes"][0] + 1
                add("error", ln, "E011", f"Notes is missing '{required}'")
        if not ({"Servings", "Yield"} & set(labels)):
            ln = rec.sections["Notes"][0] + 1
            add("error", ln, "E011", "Notes needs 'Servings' or 'Yield'")
        parts = sum(times.get(k, 0) for k in ("Prep", "Cook", "Rest"))
        total = times.get("Suggested Time")
        if total and parts and total != parts:
            ln = rec.sections["Notes"][0] + 1
            add("warn", ln, "W003",
                f"Suggested Time {total}m != Prep+Cook+Rest {parts}m")

    # E012 citation
    if "Citation" in rec.sections:
        body = " ".join(ln for _, ln in rec.section_lines("Citation"))
        if "Inspired by" not in body:
            add("error", rec.sections["Citation"][0] + 1, "E012",
                "Citation should lead with 'Inspired by:'")
    else:
        add("error", 1, "E012", "no Citation section")

    # W001 shopping item never referenced downstream
    steps = rec.steps_text().lower()
    for lineno, _cat, item in rec.shopping_items():
        head = re.split(r"[(,]", item)[0].strip().lower()
        head = re.sub(r"[^a-z\s]", "", head)
        words = [w for w in head.split() if len(w) > 3]
        if not words:
            continue
        if not any(w[:5] in steps for w in words):
            add("warn", lineno, "W001",
                f"'{item}' never appears in Prep or Cook")

    return f


# --------------------------------------------------------------------
# Stage 3b: compare against source (verify protocol)
# --------------------------------------------------------------------


STOPWORDS = {
    "fresh", "large", "small", "medium", "chopped", "diced", "sliced",
    "minced", "ground", "grated", "optional", "opt", "divided", "plus",
    "more", "taste", "and", "or", "for", "the", "into", "about", "cut",
    "peeled", "seeded", "finely", "thinly", "roughly", "packed", "room",
    "temperature", "softened", "melted", "unsalted", "kosher", "extra",
    "virgin", "freshly", "such", "like", "your", "needed", "serving",
}


def _keywords(text: str) -> set[str]:
    text = normalize_text(text.lower())
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    words = {w.rstrip("s") for w in text.split() if len(w) > 2}
    return {w for w in words if w not in STOPWORDS and w not in ABBREVS}


def compare(path: Path, source: dict) -> list[Finding]:
    f: list[Finding] = []
    rec = Recipe.load(path)
    sp = str(path)

    src_ings = source.get("ingredients") or []
    if not src_ings:
        f.append(Finding("warn", sp, 0, "V000",
                         "source record has no ingredients; fetch stage "
                         "produced nothing to compare"))
        return f

    ours = rec.shopping_items()
    our_kw = [(ln, item, _keywords(item)) for ln, _c, item in ours]
    matched_src: set[int] = set()

    for ln, item, kw in our_kw:
        if not kw:
            continue
        hit = None
        for si, s in enumerate(src_ings):
            if kw & _keywords(s):
                hit = si
                break
        if hit is None:
            f.append(Finding("warn", sp, ln, "V001",
                             f"'{item}' not found in source ingredients"))
        else:
            matched_src.add(hit)

    for si, s in enumerate(src_ings):
        if si not in matched_src:
            f.append(Finding("warn", sp, 0, "V002",
                             f"source ingredient absent from Shopping: {s}"))

    # Times and yield
    notes = {}
    for _ln, line in rec.section_lines("Notes"):
        m = re.match(r"^\s*-\s+([A-Za-z][A-Za-z ]*?):\s*(.*)$", line)
        if m:
            notes[m.group(1).strip()] = m.group(2).strip()
    nline = rec.sections.get("Notes", (0, 0))[0] + 1
    for label, key in (("Prep", "prep_minutes"), ("Cook", "cook_minutes"),
                       ("Suggested Time", "total_minutes")):
        src_v = source.get(key)
        our_v = _parse_minutes(notes.get(label, "")) if label in notes else None
        if src_v and our_v and abs(src_v - our_v) > max(5, src_v * 0.25):
            f.append(Finding("warn", sp, nline, "V003",
                             f"{label}: ours {our_v}m vs source {src_v}m"))
    if source.get("yield") and ("Servings" in notes or "Yield" in notes):
        ours_y = notes.get("Servings") or notes.get("Yield")
        src_y = source["yield"]
        if not set(re.findall(r"\d+", ours_y)) & set(re.findall(r"\d+", src_y)):
            f.append(Finding("warn", sp, nline, "V004",
                             f"yield: ours '{ours_y}' vs source '{src_y}'"))
    return f


# --------------------------------------------------------------------
# Stage 5: fmt
# --------------------------------------------------------------------

MDFORMAT_MODULES = ("mdformat", "mdformat_mkdocs", "mdformat_admon")


def find_mdformat_python() -> str | None:
    """Locate an interpreter whose mdformat matches the pre-commit hook.

    The repo's hook pins mdformat plus mdformat-mkdocs and
    mdformat-gfm. An interpreter with plain mdformat produces different
    output, so anything missing a plugin is rejected rather than used.

    Version parity was checked against the whole corpus: mdformat
    1.0.0 with mdformat-mkdocs 5.3.0 and 0.7.17 with 3.1.0 produce
    byte-identical output, so either stack is acceptable.
    """
    candidates: list[str] = []
    env = os.environ.get("COOKBOOK_MDFORMAT_PYTHON")
    if env:
        candidates.append(env)
    candidates.append(sys.executable)
    cache = Path.home() / ".cache" / "pre-commit"
    if cache.is_dir():
        candidates += [str(p) for p in sorted(cache.glob("repo*/py_env*/bin/python"))]
    probe = "import " + ", ".join(MDFORMAT_MODULES)
    for cand in candidates:
        if not Path(cand).exists():
            continue
        try:
            r = subprocess.run([cand, "-c", probe], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return cand
    return None


def run_fmt(paths: list[Path], check: bool = False) -> int:
    py = find_mdformat_python()
    if not py:
        sys.stderr.write(
            "fmt: no interpreter found with "
            + ", ".join(MDFORMAT_MODULES)
            + ".\nCreate one, then re-run:\n"
            "  python3 -m venv .mdfmt\n"
            "  .mdfmt/bin/pip install mdformat==0.7.17 mdformat-mkdocs \\\n"
            "      mdformat-gfm 'more_itertools<11'\n"
            "  COOKBOOK_MDFORMAT_PYTHON=$PWD/.mdfmt/bin/python "
            "python3 recipe_tools.py fmt ...\n"
        )
        return 2
    cmd = [py, "-m", "mdformat", "--wrap", "80"]
    if check:
        cmd.append("--check")
    cmd += [str(p) for p in paths]
    r = subprocess.run(cmd, capture_output=True, text=True)
    # mdformat-mkdocs 5.x bundles admonition support, so installing
    # mdformat-admon alongside it makes mdformat announce a renderer
    # conflict on every run. Output is unaffected - parity with the
    # hook's stack was verified over the whole corpus - so drop the
    # noise but keep everything else.
    noise = re.compile(r"^(Warning: )?Plugin conflict\.")
    for stream, sink in ((r.stdout, sys.stdout), (r.stderr, sys.stderr)):
        kept = [ln for ln in (stream or "").splitlines() if not noise.match(ln)]
        if kept:
            sink.write("\n".join(kept) + "\n")
    return r.returncode


# --------------------------------------------------------------------
# Stage 6: checklist
# --------------------------------------------------------------------

TABLE_HEAD = "| Recipe | Source | Checked | Status | Lint | Summary |"
TABLE_RULE = "| :----- | :----- | :------ | :----- | :--- | :------ |"


def _index_sources(repo: Path) -> dict[str, str]:
    """Map recipe path -> source URL, read from docs/src/index.md."""
    idx = repo / "docs" / "src" / "index.md"
    if not idx.exists():
        return {}
    out = {}
    text = idx.read_text(encoding="utf-8")
    for row in text.splitlines():
        cells = [c.strip() for c in row.split("|")]
        page = next((c for c in cells if re.search(r"\(\./[^)]+\.md\)", c)), None)
        link = next((c for c in cells if re.search(r"\(https?://", c)), None)
        if not page:
            continue
        rel = re.search(r"\(\./([^)]+\.md)\)", page).group(1)
        url = re.search(r"\((https?://[^)]+)\)", link).group(1) if link else ""
        out[rel] = url
    return out


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def checklist_rows(path: Path) -> tuple[list[str], int, int]:
    """Return (lines, table_start, table_end) for the status table."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    start = end = -1
    for i, ln in enumerate(lines):
        if ln.strip().startswith("| Recipe ") and start == -1:
            start = i
        elif start != -1 and not ln.strip().startswith("|"):
            end = i
            break
    if start != -1 and end == -1:
        end = len(lines)
    return lines, start, end


def checklist_upsert(path: Path, recipe: str, status: str, summary: str,
                     source: str | None = None,
                     lint: str | None = None,
                     when: str | None = None) -> None:
    when = when or datetime.date.today().strftime(DATE_FMT)
    lines, start, end = checklist_rows(path)
    if start == -1:
        raise SystemExit(
            f"{path}: no status table found. Run `checklist --init` first."
        )
    existing_source = ""
    target = None
    for i in range(start + 2, end):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if cells and cells[0] == recipe:
            target = i
            if len(cells) > 1:
                existing_source = cells[1]
            break
    row = "| {} | {} | {} | {} | {} | {} |".format(
        recipe,
        _escape_cell(source if source is not None else existing_source) or "-",
        when,
        status,
        _escape_cell(lint or "-") or "-",
        _escape_cell(summary) or "-",
    )
    if target is not None:
        lines[target] = row
    else:
        lines.insert(end, row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------


def _recipe_paths(repo: Path, args_paths: list[str]) -> list[Path]:
    if args_paths:
        return [Path(p) for p in args_paths]
    return sorted((repo / "docs" / "src").glob("*/*.md"))


# Markers of a bot interstitial. Deliberately excludes
# "challenge-platform": Cloudflare injects that script into normal pages
# too, so matching it would reject real recipes.
CHALLENGE = re.compile(
    r"<title>\s*(just a moment|security verification|attention required)"
    r"|cf-browser-verification"
    r"|enable javascript and cookies to continue",
    re.I,
)


def cmd_fetch(args) -> int:
    try:
        page = http_get(args.url)
    except urllib.error.HTTPError as e:
        sys.stderr.write(
            f"fetch: HTTP {e.code} {e.reason} for {args.url}\n"
            "This host blocks every scripted request, WebFetch included, "
            "so it needs a real browser:\n"
            "  ./copy_from_web.sh -s <recipe>   # opens the page source\n"
            "  ./copy_from_web.sh -p <recipe>   # clipboard -> stub\n"
        )
        return 3
    except Exception as e:  # noqa: BLE001 - surface any transport error
        sys.stderr.write(f"fetch: {type(e).__name__}: {e}\n")
        return 3
    # A 200 can still be an interstitial rather than the recipe.
    if CHALLENGE.search(page[:4000]):
        sys.stderr.write(
            f"fetch: {args.url} returned a bot-challenge page, not the "
            "recipe.\nUse the browser path: copy_from_web.sh -s <recipe>\n"
        )
        return 3
    if args.html:
        Path(args.html).write_text(page, encoding="utf-8")
        print(f"wrote {args.html} ({len(page)} bytes)")
    rec = extract_source(page, args.url)
    _emit_json(rec, args.out)
    if rec["extracted_via"] == "none":
        sys.stderr.write("fetch: no JSON-LD Recipe; see 'note' in output\n")
        return 4
    return 0


def cmd_parse(args) -> int:
    path = Path(args.file)
    headers, body = read_stub(path)
    url = args.url or headers.get("url", "")

    if headers and not body.strip():
        sys.stderr.write(
            f"parse: {path} is staged but empty.\n"
            f"Paste the page text below the marker line, then re-run.\n"
        )
        return 5

    raw = body if headers else path.read_text(encoding="utf-8", errors="replace")

    if raw.lstrip().startswith("{"):
        rec = json.loads(raw)
        rec.setdefault("url", url)
    elif "<script" in raw or "<html" in raw.lower():
        rec = extract_source(raw, url)
        if rec["extracted_via"] == "none":
            rec = extract_pasted_text(_strip_tags(raw), url)
    else:
        rec = extract_pasted_text(raw, url)

    if headers.get("recipe"):
        rec["recipe"] = headers["recipe"]
    if not rec.get("name") and headers.get("title"):
        rec["name"] = headers["title"]
    _emit_json(rec, args.out)
    if not rec.get("ingredients"):
        sys.stderr.write("parse: no ingredients found; see 'note' in output\n")
        return 4
    return 0


CANONICAL = (
    re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', re.I),
    re.compile(r'property=["\']og:url["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']', re.I),
)


def page_identity(text: str) -> str | None:
    """The URL a saved page claims to be, from canonical or og:url."""
    for pat in CANONICAL:
        m = pat.search(text)
        if m:
            return m.group(1).strip().rstrip("/")
    return None


def _host(url: str) -> str:
    h = re.sub(r"^https?://", "", url).split("/")[0]
    return h[4:] if h.startswith("www.") else h


def cmd_check(args) -> int:
    """Confirm a saved page is the page it was meant to be.

    Catches two things the eye misses in a bulk collection: a bot
    interstitial saved as if it were the recipe, and the wrong page
    captured because the clipboard still held the previous one.
    """
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    if CHALLENGE.search(text[:4000]):
        print(f"{args.file}: interstitial, not a recipe page")
        return 3
    got = page_identity(text)
    want = args.url.split("#")[0].rstrip("/")
    if not got:
        print(f"{args.file}: no canonical url in the page; cannot verify")
        return 2
    if _host(got) != _host(want):
        print(f"{args.file}: WRONG PAGE\n  page says: {got}\n  expected : {want}")
        return 1
    if got != want:
        print(f"{args.file}: host matches, path moved (redirect?)\n"
              f"  page says: {got}\n  expected : {want}")
        return 0
    print(f"{args.file}: ok")
    return 0


def cmd_sources(args) -> int:
    """List every recipe and its source URL, tab separated.

    Exists so shell callers reuse this parser instead of re-deriving it
    from index.md with grep.
    """
    repo = Path(args.repo)
    srcs = _index_sources(repo)
    for p in sorted((repo / "docs" / "src").glob("*/*.md")):
        rel = f"{p.parent.name}/{p.name}"
        url = srcs.get(rel, "")
        if args.with_url and not url:
            continue
        print(f"{rel}\t{url}")
    return 0


def cmd_stage(args) -> int:
    """Create paste stubs for recipes whose source blocks scripted GETs."""
    repo = Path(args.repo)
    outdir = Path(args.dir)
    outdir.mkdir(parents=True, exist_ok=True)
    sources = _index_sources(repo)

    if args.recipe:
        wanted = list(args.recipe)
    else:
        wanted = [
            f"{p.parent.name}/{p.name}"
            for p in sorted((repo / "docs" / "src").glob("*/*.md"))
        ]

    made, skipped = 0, []
    for rel in wanted:
        url = sources.get(rel, "")
        if not url:
            skipped.append(f"{rel} (no source URL in index.md)")
            continue
        stub = outdir / (Path(rel).stem + ".txt")
        if stub.exists() and not args.force:
            _h, body = read_stub(stub)
            if body.strip():
                skipped.append(f"{stub.name} (already pasted)")
                continue
        stub.write_text(
            f"URL: {url}\n"
            f"RECIPE: {rel}\n"
            f"TITLE:\n"
            f"\n"
            f"Open the URL, select the recipe card, and paste it below the\n"
            f"marker. Surrounding story text is fine - it gets ignored.\n"
            f"\n"
            f"{STUB_MARKER}\n",
            encoding="utf-8",
        )
        made += 1
    print(f"{outdir}: {made} stub(s) written")
    for sk in skipped:
        print(f"  skipped {sk}")
    return 0


def cmd_normalize(args) -> int:
    if args.in_place:
        for p in (Path(x) for x in args.files):
            original = p.read_text(encoding="utf-8")
            updated = normalize_text(original)
            if updated != original:
                p.write_text(updated, encoding="utf-8")
                print(f"normalized {p}")
        return 0
    text = (
        sys.stdin.read()
        if not args.files or args.files == ["-"]
        else "\n".join(Path(x).read_text(encoding="utf-8") for x in args.files)
    )
    sys.stdout.write(normalize_text(text))
    return 0


def cmd_lint(args) -> int:
    repo = Path(args.repo)
    findings: list[Finding] = []
    paths = _recipe_paths(repo, args.files)
    for p in paths:
        findings += lint_recipe(p)
    return _report(findings, paths, args)


def cmd_compare(args) -> int:
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    p = Path(args.recipe)
    findings = compare(p, source)
    if not args.no_lint:
        findings += lint_recipe(p)
    return _report(findings, [p], args)


def cmd_fmt(args) -> int:
    paths = _recipe_paths(Path(args.repo), args.files)
    return run_fmt(paths, check=args.check)


def cmd_checklist(args) -> int:
    path = Path(args.path)
    if args.init:
        return _checklist_init(path, Path(args.repo), args.force)
    if not args.recipe:
        lines, start, end = checklist_rows(path)
        if start == -1:
            sys.stderr.write(f"{path}: no status table\n")
            return 2
        sys.stdout.write("\n".join(lines[start:end]) + "\n")
        return 0
    checklist_upsert(
        path,
        recipe=args.recipe,
        status=args.status,
        summary=args.summary or "-",
        source=args.source,
        lint=args.lint,
        when=args.date,
    )
    print(f"{path}: updated {args.recipe}")
    return 0


def _checklist_init(path: Path, repo: Path, force: bool) -> int:
    lines, start, _end = checklist_rows(path)
    if start != -1 and not force:
        sys.stderr.write(
            f"{path} already has a status table; pass --force to rebuild\n"
        )
        return 2
    sources = _index_sources(repo)
    rows = []
    for p in sorted((repo / "docs" / "src").glob("*/*.md")):
        rel = f"{p.parent.name}/{p.name}"
        empty = not p.read_text(encoding="utf-8").strip()
        url = sources.get(rel, "")
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0] if url else "-"
        rows.append(
            "| {} | {} | - | {} | {} | {} |".format(
                rel,
                host,
                "blocked" if empty else "pending",
                "-",
                "empty file; needs a full draft" if empty else "not yet checked",
            )
        )
    table = "\n".join([TABLE_HEAD, TABLE_RULE] + rows)
    if start == -1:
        body = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(body.rstrip() + "\n\n" + table + "\n", encoding="utf-8")
    else:
        lines2, s, e = checklist_rows(path)
        path.write_text(
            "\n".join(lines2[:s] + table.splitlines() + lines2[e:]) + "\n",
            encoding="utf-8",
        )
    print(f"{path}: wrote {len(rows)} rows")
    return 0


def _emit_json(obj: dict, out: str | None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        sys.stdout.write(text + "\n")


def _report(findings: list[Finding], paths: list[Path], args) -> int:
    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level == "warn"]
    shown = findings if args.warnings else errors
    for f in sorted(shown, key=lambda x: (x.path, x.line, x.code)):
        print(f.render())
    print(
        f"\n{len(paths)} file(s): {len(errors)} error(s), "
        f"{len(warns)} warning(s)"
        + ("" if args.warnings else " (-w to show warnings)")
    )
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="recipe_tools.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--repo", default=".", help="repo root (default: .)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="URL -> source record JSON")
    p.add_argument("url")
    p.add_argument("-o", "--out")
    p.add_argument("--html", help="also save the raw page to this path")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("parse", help="saved page or JSON -> source record")
    p.add_argument("file")
    p.add_argument("--url", default="")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("check", help="is this saved page the right page?")
    p.add_argument("file")
    p.add_argument("--url", required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("sources", help="list recipes and their source URLs")
    p.add_argument("--with-url", action="store_true",
                   help="skip recipes that have no source URL")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("stage", help="write paste stubs for blocked sources")
    p.add_argument("dir", help="directory for the .txt stubs")
    p.add_argument("--recipe", action="append",
                   help="e.g. 1_Entrees/TomatoSoup.md; repeatable")
    p.add_argument("--force", action="store_true",
                   help="overwrite a stub even if it already has pasted text")
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("normalize", help="apply house units and decimals")
    p.add_argument("files", nargs="*")
    p.add_argument("-i", "--in-place", action="store_true")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("lint", help="check recipes against template.md")
    p.add_argument("files", nargs="*")
    p.add_argument("-w", "--warnings", action="store_true")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("compare", help="recipe vs source record")
    p.add_argument("recipe")
    p.add_argument("--source", required=True)
    p.add_argument("-w", "--warnings", action="store_true", default=True)
    p.add_argument("--no-lint", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("fmt", help="mdformat --wrap 80, hook-identical")
    p.add_argument("files", nargs="*")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_fmt)

    p = sub.add_parser("checklist", help="read or update the status table")
    p.add_argument("path")
    p.add_argument("--init", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--recipe")
    p.add_argument("--status", choices=STATUSES, default="verified")
    p.add_argument("--summary")
    p.add_argument("--source")
    p.add_argument("--lint")
    p.add_argument("--date")
    p.set_defaults(func=cmd_checklist)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
