#!/usr/bin/env bash
# Collect source HTML for every recipe, automatically where possible and
# with one keystroke from you where it is not.
#
# A bare run does the whole job:
#   1. fetches every page a script is allowed to fetch, into
#      <dir>/<Recipe>.html
#   2. marks the rest as attempted, so later runs skip straight past them
#   3. then, for each remaining recipe in turn, opens the page source in
#      Firefox and waits while you press Ctrl+A then Ctrl+C; it picks the
#      page up off the clipboard and files it
#
# Usage:  ./copy_from_web.sh [options] [recipe]
#
#   recipe    TomatoSoup | 1_Entrees/TomatoSoup.md | path to a stub.
#             Omit it to sweep everything outstanding.
#
# Options:
#   -F, --fetch-only  stop after the automatic phase; never wait on you
#   -M, --manual-only skip the automatic phase
#   -m, --max N       handle at most N recipes by hand this run
#   -l, --list        show every recipe and whether its HTML is in hand
#   -s, --source      open view-source: (default for the manual phase)
#   -o, --open        open the rendered page instead
#   -S, --save        use Firefox's Save-As instead of the clipboard:
#                     puts the destination path on the clipboard and
#                     waits for the file. Ctrl+L in the dialog, paste
#   -c, --copy        copy the URL to the clipboard (single recipe only)
#   -d, --download    try the automatic fetch for one recipe
#   -a, --adopt FILE  file a page you already saved
#   -p, --paste       write the clipboard into the stub, below the marker
#   -n, --no-browser  do not touch the browser
#       --keep-tabs   leave each tab open; by default the previous
#                     recipe's tab is closed as the next one opens
#   -D, --dir DIR     stub directory (default: $PASTE_DIR or .claude/pasted)
#   -f, --force       overwrite HTML or stub content that already exists
#       --retry       re-attempt fetches previously marked as failed
#       --dry-run     print what would happen, change nothing
#   -h, --help        this text
#
# Env: PASTE_DIR, COOKBOOK_PYTHON

set -uo pipefail

MARKER='--- PASTE PAGE TEXT BELOW THIS LINE ---'
ATTEMPT_KEY='AUTOFETCH'
DIR="${PASTE_DIR:-.claude/pasted}"
PYTHON="${COOKBOOK_PYTHON:-python3}"
TOOL=recipe_tools.py

do_copy=0 do_open=0 do_source=0 do_dl=0 do_paste=0 do_list=0
force=0 dry=0 no_browser=0 explicit=0 fetch_only=0 manual_only=0
retry=0 maxn=0 adopt= recipe= via_save=0 keep_tabs=0 last_url=

die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'warn:  %s\n' "$*" >&2; }
usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while (($#)); do
  case $1 in
    -c|--copy)        do_copy=1;   explicit=1 ;;
    -o|--open)        do_open=1;   explicit=1 ;;
    -s|--source)      do_source=1; do_open=1; explicit=1 ;;
    -d|--download)    do_dl=1;     explicit=1 ;;
    -p|--paste)       do_paste=1;  explicit=1 ;;
    -a|--adopt)       adopt=${2:?--adopt needs a file}; explicit=1; shift ;;
    -F|--fetch-only)  fetch_only=1 ;;
    -M|--manual-only) manual_only=1 ;;
    -m|--max)         maxn=${2:?--max needs a number}; shift ;;
    -n|--no-browser)  no_browser=1 ;;
    -f|--force)       force=1 ;;
    -l|--list)        do_list=1 ;;
    -D|--dir)         DIR=${2:?--dir needs a value}; shift ;;
    --retry)          retry=1 ;;
    -S|--save)        via_save=1 ;;
    --keep-tabs)      keep_tabs=1 ;;
    --dry-run)        dry=1 ;;
    -h|--help)        usage ;;
    -*)               die "unknown option: $1" ;;
    *)                recipe=$1 ;;
  esac
  shift
done

[[ -d $DIR ]] || die "stub directory not found: $DIR"
[[ -f $TOOL ]] || die "$TOOL not found; run from the repo root"
command -v "$PYTHON" >/dev/null || die "python not found: $PYTHON
set COOKBOOK_PYTHON to an interpreter"

if [[ -f $TOOL ]] && ! grep -qF "\"$MARKER\"" "$TOOL"; then
  warn "MARKER does not match $TOOL STUB_MARKER"
fi

# ------------------------------------------------------------- helpers

clip_write() {
  if   command -v xclip   >/dev/null; then xclip -selection c -rmlastnl
  elif command -v wl-copy >/dev/null; then wl-copy
  else die "no clipboard tool (install xclip or wl-clipboard)"; fi
}
clip_read() {
  if   command -v xclip    >/dev/null; then xclip -selection c -o
  elif command -v wl-paste >/dev/null; then wl-paste
  else die "no clipboard tool (install xclip or wl-clipboard)"; fi
}
need_display() {
  [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]] || \
    die "no DISPLAY/WAYLAND_DISPLAY; the clipboard needs a graphical session"
}

stub_of()  { printf '%s/%s.txt' "$DIR" "$1"; }
html_of()  { printf '%s/%s.html' "$DIR" "$1"; }
stub_body() { awk -v m="$MARKER" 'f{print} index($0,m){f=1}' "$1"; }

have_html() { [[ -s $(html_of "$1") ]]; }

attempted() {  # has the automatic fetch already been ruled out?
  local st; st=$(stub_of "$1")
  [[ -f $st ]] && grep -q "^$ATTEMPT_KEY: failed" "$st"
}

mark_attempted() {  # $1 base, $2 rel path, $3 reason
  local st; st=$(stub_of "$1")
  if [[ ! -f $st ]]; then
    "$PYTHON" "$TOOL" stage "$DIR" --recipe "$2" >/dev/null 2>&1 || true
  fi
  [[ -f $st ]] || return 0
  grep -v "^$ATTEMPT_KEY:" "$st" >"$st.tmp"
  # Keep the marker block intact: insert the note into the header.
  awk -v k="$ATTEMPT_KEY" -v v="failed $(date +%F) $3" \
      -v m="$MARKER" 'index($0,m){print k": "v; print; next} {print}' \
      "$st.tmp" >"$st"
  rm -f "$st.tmp"
}

# Every recipe that has a source URL, as "base<TAB>rel<TAB>url".
# Staged stubs are included too, so a recipe being collected for the first
# time - which has no file and so no Citation yet - still gets swept.
worklist() {
  local seen=" "
  "$PYTHON" "$TOOL" sources --with-url | while IFS=$'\t' read -r rel url; do
    local b; b=$(basename "$rel" .md)
    printf '%s\t%s\t%s\n' "$b" "$rel" "$url"
  done
  for f in "$DIR"/*.txt; do
    [[ -e $f ]] || continue
    local b u r
    b=$(basename "$f" .txt)
    "$PYTHON" "$TOOL" sources | grep -q "/$b\.md" && continue
    u=$(sed -n 's/^URL:[[:space:]]*//p' "$f" | head -1)
    r=$(sed -n 's/^RECIPE:[[:space:]]*//p' "$f" | head -1)
    [[ -n $u ]] && printf '%s\t%s\t%s\n' "$b" "${r:-$b.md}" "$u"
  done
}

report_html() {  # $1 file, $2 expected url
  local n out rc
  n=$(grep -c 'application/ld+json' "$1" 2>/dev/null || true)
  # Delegate identity checking to recipe_tools.py: it rejects an
  # interstitial saved as a recipe, and a page captured from a stale
  # clipboard (wrong host), while tolerating a plain redirect.
  out=$("$PYTHON" "$TOOL" check "$1" --url "$2" 2>&1); rc=$?
  if ((rc == 1 || rc == 3)); then
    printf '    REJECTED: %s\n' "$(printf '%s' "$out" | head -1 | sed 's|.*html: ||')"
    printf '%s\n' "$out" | tail -n +2 | sed 's/^/    /'
    return 1
  fi
  printf '    %s bytes, %s ld+json block(s)%s\n' "$(wc -c <"$1")" "$n" \
    "$([[ ${n:-0} -gt 0 ]] && echo ' - parses exactly' || \
       echo ' - heuristic parse only')"
  ((rc == 2)) && printf '    note: page carries no canonical url\n'
  printf '%s\n' "$out" | grep -q 'path moved' && \
    printf '    note: source redirected; update index.md\n'
  return 0
}

# ---------------------------------------------------------------- list

if ((do_list)); then
  printf '%-30s %-6s %-10s %s\n' RECIPE HTML AUTOFETCH HOST
  printf '%s\n' '(AUTOFETCH=failed is retriable: hosts vary. use --retry)'
  while IFS=$'\t' read -r b rel url; do
    h=${url#*://}; h=${h%%/*}; h=${h#www.}
    have_html "$b" && s=yes || s=-
    attempted "$b" && a=failed || a=-
    printf '%-30s %-6s %-10s %s\n' "$b" "$s" "$a" "$h"
  done < <(worklist)
  exit 0
fi

# ------------------------------------------------- single-recipe mode

resolve_url() {  # $1 base -> echoes url
  local st; st=$(stub_of "$1")
  if [[ -f $st ]]; then
    sed -n 's/^URL:[[:space:]]*//p' "$st" | head -1
  else
    worklist | awk -F'\t' -v b="$1" '$1==b{print $3; exit}'
  fi
}

auto_fetch() {  # $1 base, $2 url -> 0 ok, 1 failed
  local out; out=$(html_of "$1")
  if ((dry)); then printf 'would fetch %s -> %s\n' "$2" "$out"; return 0; fi
  if "$PYTHON" "$TOOL" fetch "$2" --html "$out" \
       -o "$DIR/$1.json" >/dev/null 2>&1; then
    report_html "$out" "$2" && return 0
    rm -f "$out" "$DIR/$1.json"; return 1
  fi
  rm -f "$out" "$DIR/$1.json"; return 1
}

# Close the tab opened for the previous recipe. Firefox has no CLI for
# this, so it goes through xdotool - but only after matching the window
# title against the URL we opened. A bare Ctrl+W would otherwise close
# whatever tab the user had switched to.
close_tab() {  # $1 url previously opened
  local url=${1%%#*} id name
  [[ -n $url ]] || return 1
  command -v xdotool >/dev/null || return 1
  for id in $(xdotool search --class firefox 2>/dev/null); do
    name=$(xdotool getwindowname "$id" 2>/dev/null) || continue
    [[ $name == *"$url"* ]] || continue
    xdotool windowactivate --sync "$id" 2>/dev/null || continue
    # XTEST to the focused window; Firefox ignores synthetic per-window
    # events, so activate first and then type.
    xdotool key ctrl+w 2>/dev/null && return 0
  done
  return 1
}

open_tab() {  # $1 url, $2 use-view-source
  command -v firefox >/dev/null || die "firefox not on PATH"
  local target=$1
  (($2)) && target="view-source:$1"
  if ((dry)); then printf 'would run: firefox --new-tab %s\n' "$target"
  else firefox --new-tab "$target" >/dev/null 2>&1 || \
         warn "firefox --new-tab returned non-zero"; fi
}

# Watch the clipboard until it holds a page. Firefox's Save-As dialog is
# awkward to drive, and Ctrl+A/Ctrl+C on a view-source tab yields exactly
# the same bytes, so the clipboard is the better channel.
# Returns 0 captured, 1 skip, 3 quit.
wait_for_clip() {  # $1 destination file
  local f=$1 size
  if [[ ! -t 0 ]]; then
    printf '    not a tty, so not waiting. Ctrl+A/Ctrl+C in the tab,\n'
    printf '    then: %s -p %s\n' "$0" "$(basename "${f%.html}")"
    return 1
  fi
  printf '    waiting for the clipboard   [Enter = skip, q = quit]\n'
  while :; do
    size=$(clip_read 2>/dev/null | wc -c)
    if ((size > 1000)); then
      clip_read >"$f" 2>/dev/null
      if grep -q '<' "$f" 2>/dev/null; then
        printf '    captured %s bytes.\n' "$size"
        return 0
      fi
      rm -f "$f"
    fi
    if IFS= read -r -t 2 -n 1 key; then
      case $key in q|Q) return 3 ;; *) return 1 ;; esac
    fi
  done
}

# Alternative: wait for the user to Ctrl+S to the destination path.
wait_for_save() {  # $1 file
  local f=$1 a b
  if [[ ! -t 0 ]]; then
    printf '    not a tty, so not waiting.\n'; return 1
  fi
  printf '    waiting for the file   [Enter = skip, q = quit]\n'
  while :; do
    if [[ -s $f ]]; then
      a=$(wc -c <"$f"); sleep 1; b=$(wc -c <"$f")
      [[ $a == "$b" ]] && { printf '    saved.\n'; return 0; }
      continue
    fi
    if IFS= read -r -t 2 -n 1 key; then
      case $key in q|Q) return 3 ;; *) return 1 ;; esac
    fi
  done
}

manual_one() {  # $1 base, $2 url -> 0 got it, 1 skipped, 3 quit
  local b=$1 url=$2 out abs rc
  out=$(html_of "$b"); abs="$PWD/${out#./}"
  printf '\n%s\n  %s\n' "$b" "$url"

  if ((!keep_tabs && !no_browser && !dry)) && [[ -n $last_url ]]; then
    close_tab "$last_url" && printf '  closed the previous tab\n'
  fi

  if ((via_save)); then
    ((dry)) || printf '%s' "$abs" | clip_write
    printf '  clipboard: %s\n' "$abs"
    ((no_browser)) || { open_tab "$url" 1; last_url=$url; }
    printf '  in Firefox: Ctrl+S, then Ctrl+L in the dialog and paste\n'
    ((dry)) && return 1
    wait_for_save "$out"; rc=$?
  else
    # Reset the clipboard so a stale page cannot be mistaken for this one.
    ((dry)) || printf 'cookbook:waiting:%s' "$b" | clip_write
    ((no_browser)) || { open_tab "$url" 1; last_url=$url; }
    printf '  in Firefox: Ctrl+A then Ctrl+C in the view-source tab\n'
    ((dry)) && return 1
    wait_for_clip "$out"; rc=$?
  fi

  if ((rc == 0)); then
    report_html "$out" "$url" || { rm -f "$out"; rc=1; }
  fi
  return $rc
}

if [[ -n $recipe || -n $adopt ]]; then
  base=$(basename "${recipe:-x}"); base=${base%.txt}; base=${base%.md}
  [[ -n $recipe ]] || die "--adopt needs a recipe name too"
  url=$(resolve_url "$base")
  [[ -n $url ]] || die "no URL found for $base"
  stub=$(stub_of "$base")
  printf 'recipe: %s\nurl:    %s\n' "$base" "$url"

  if [[ -n $adopt ]]; then
    [[ -f $adopt ]] || die "no such file: $adopt"
    out=$(html_of "$base")
    [[ -e $out && $force -eq 0 ]] && die "$out exists; pass --force"
    if ((dry)); then printf 'would file %s as %s\n' "$adopt" "$out"
    else cp -- "$adopt" "$out"; printf 'adopted -> %s\n' "$out"
         report_html "$out" "$url" || rm -f "$out"; fi
  fi

  if ((do_dl)); then
    if auto_fetch "$base" "$url"; then printf 'download: ok\n'
    else printf 'download: not permitted by host; use the manual path\n'; fi
  fi

  if ((do_paste)); then
    need_display
    [[ -f $stub ]] || die "no stub to paste into: $stub"
    existing=$(stub_body "$stub" | tr -d '[:space:]')
    [[ -n $existing && $force -eq 0 ]] && \
      die "$stub already has content; pass --force"
    tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
    clip_read >"$tmp" || die "could not read the clipboard"
    bytes=$(wc -c <"$tmp")
    ((bytes > 200)) || die "clipboard holds only $bytes bytes - copy the page"
    [[ $(head -c 2000 "$tmp") == *"$MARKER"* ]] && \
      die "the clipboard holds the stub's own marker, not a web page"
    if ((dry)); then printf 'would write %s bytes into %s\n' "$bytes" "$stub"
    else { awk -v m="$MARKER" '{print} index($0,m){exit}' "$stub"; \
           cat "$tmp"; } >"$stub.new"
         mv "$stub.new" "$stub"
         printf 'pasted %s bytes into %s\n' "$bytes" "$stub"; fi
  fi

  # Bare "recipe" with no action flags: do the manual hand-off for it.
  if ((!explicit)); then
    need_display
    manual_one "$base" "$url"
  fi
  exit 0
fi

# ----------------------------------------------------------- sweep mode

printf 'sweep: %s\n' "$DIR"

fetched=0 failed=0 already=0
if ((!manual_only)); then
  printf '\n== automatic fetch ==\n'
  while IFS=$'\t' read -r b rel url; do
    if have_html "$b"; then already=$((already + 1)); continue; fi
    if ((!retry)) && attempted "$b"; then continue; fi
    printf '  %s\n' "$b"
    ((dry)) || sleep 1   # one request per second, not a flood
    if auto_fetch "$b" "$url"; then
      fetched=$((fetched + 1))
    else
      failed=$((failed + 1))
      ((dry)) || mark_attempted "$b" "$rel" "host refused"
      printf '    refused - marked; retriable later with --retry\n'
    fi
  done < <(worklist)
  printf 'fetched %s, refused %s, already had %s\n' \
    "$fetched" "$failed" "$already"
fi

((fetch_only)) && exit 0

# Remaining: no HTML in hand.
mapfile -t pending < <(worklist | while IFS=$'\t' read -r b rel url; do
  have_html "$b" || printf '%s\t%s\t%s\n' "$b" "$rel" "$url"
done)

printf '\n== manual (%s outstanding) ==\n' "${#pending[@]}"
((${#pending[@]})) || { printf 'nothing left to collect.\n'; exit 0; }

need_display
saved=0 skipped=0 n=0
for row in "${pending[@]}"; do
  IFS=$'\t' read -r b rel url <<<"$row"
  ((maxn > 0 && n >= maxn)) && { printf '\nreached --max %s\n' "$maxn"; break; }
  n=$((n + 1))
  manual_one "$b" "$url"; rc=$?
  case $rc in
    0) saved=$((saved + 1)) ;;
    3) printf '\nstopped at your request.\n'; break ;;
    *) skipped=$((skipped + 1)) ;;
  esac
  ((dry)) && break
done
if ((!keep_tabs && !no_browser && !dry)) && [[ -n $last_url ]]; then
  close_tab "$last_url" && printf '\nclosed the last tab\n'
fi

printf '\nsaved %s, skipped %s, %s still outstanding\n' \
  "$saved" "$skipped" "$((${#pending[@]} - saved))"
