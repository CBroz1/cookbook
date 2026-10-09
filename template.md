# Title

<!-- Title Case. Dish name only. Match the page name used in index.md. -->

!!! note
    Whole-recipe caveats go here. Delete this block if there are none.

## Shopping

<!-- One grocery run, in store-layout order. Keep all four categories. -->

- Produce
    - item (qty)
- Dairy
    - item (qty)
- Middle
    - item (qty)
- Staples
    - item (qty)

## Prep

<!-- Everything doable before heat. Group by destination vessel. -->

1. Single action
1. Vessel or group name
    - item (qty)
    - item (qty)

## Cook

<!-- First heat to plate, in wall-clock order. -->

1. Oven to 000
1. Vessel, heat level, fat (qty)
    1. Add item (qty), sensory cue (00m)
    1. Add item (qty), sensory cue (00m)
1. Garnish and serve

## Notes

- Suggested Time: 00m
    - Prep: 00m
    - Cook: 00m
- Servings: 0

## Citation

Inspired by: [Source Name](https://example.com/recipe)

<!-- ==================================================================
     DELETE FROM HERE DOWN when starting a new recipe.
     Everything below is the house style guide, not recipe content.
     ================================================================== -->

## Conventions

Goal: a cook with limited attention can execute from a phone screen in a busy
kitchen. Every line should be scannable, unambiguous, and actionable.

### Title

- Title Case, dish name only. No adjectives from the source headline.
- Match the page name in `index.md` so the table and the file agree.
- A caveat that applies to the whole recipe goes directly under the H1, before
    `## Shopping`, as a `!!! note` callout.

### Shopping

- Purpose: a single grocery run. Ordered by store layout, not by use.
- Four fixed categories, always present, always in this order:
    - `Produce` - produce section, including fresh herbs
    - `Dairy` - refrigerated case, including eggs
    - `Middle` - center aisles: cans, dry goods, noodles, meat, frozen
    - `Staples` - already at home; check the pantry before shopping
- Sorting test: would you buy it on this trip? If yes, `Middle`. If it is always
    stocked, `Staples`. Keep a given item in the same category across recipes.
- Leave an empty category as a bare bullet. The four-line scan matters more than
    brevity.
- Categories are list items, never headings, and carry no trailing colon.
- One ingredient per line, name first, quantity in parentheses: `broth (32oz)`.
    Name first keeps the list scannable.
- Optional items end with `, opt`: `poblano pepper (1, opt)`.
- Alternatives use `or`: `pistachios (.33c) or pepitas (.5c)`.
- List only things you buy. Intermediate products - pasta water, the dressing,
    the roux - belong in Prep or Cook.

### Prep

- Everything doable before heat: wash, chop, press, grate, measure, mix sauces,
    bring to room temperature, soak, marinate.
- Group by destination vessel, not by ingredient type. Name the vessel or the
    group so the cook knows where it is going: `Mix sauce`, `Spice mix B`,
    `Bowl A`, `Set aside`.
- Repeat every quantity here. The cook should never scroll back up to Shopping
    mid-task.
- Put long-lead items first and flag them in a callout under the heading, before
    step 1.
- A single action is one line. A step that fills a vessel becomes a parent line
    plus a `-` sublist of its contents.
- Leave `## Prep` empty only if the dish genuinely has no prep.

### Cook

- Everything from first heat to plate, in wall-clock order.
- Oven preheat is the first step whenever the oven is used.
- Open each heat step with vessel, heat level, then fat:
    `Large skillet, medium heat, oil (1tbsp)`.
- Reuse a vessel with `Same pan` or `Same skillet`.
- Sub-steps for one vessel nest under it as `1.` items, so the cook reads one
    pan at a time.
- Give every endpoint a sensory cue and a time: `Cook until fragrant (30s)`. Cue
    first, time in parentheses.
- Garnish and serve is the last step.
- Multi-component dishes may split Prep and Cook into `###` subsections per
    component (e.g. `### Sauce`, `### Onions`). Use this only when components
    are cooked separately.

### Notes

- Fixed labels, in this order:
    - `Suggested Time:` total, with `Prep:` and `Cook:` sub-bullets. Add `Rest:`
        when unattended time is significant.
    - `Servings:` a count. Use `Yield:` when the output is a volume, such as a
        sauce.
    - `Substitutions:` only when a real substitution exists.
- Total time equals the sum of its sub-bullets.
- Delete any label that does not apply. Never ship a placeholder `X`.

### Citation

- `Inspired by:` followed by the markdown link. Real source URLs are long enough
    that the formatter wraps the link to its own line - let it.
- Join multiple sources with ` &` at the end of the line.
- Non-web source needs no link: `Inspired by: Aunt Chrissy`.

### Callouts

Caveats render as MkDocs callouts, via the `admonition` extension enabled in
`docs/mkdocs.yml`.

```text
!!! note
    Rice takes time. Start it first.
```

- Marker line, then the body indented 4 spaces, with no blank line between. That
    is what `mdformat` emits - it strips the blank line if you add one.
- `!!! note` is the default. Use `!!! warning` only for something that ruins the
    dish if missed.
- An optional short title goes in quotes: `!!! note "Timing"`. Without one, the
    callout header reads "Note".
- Whole-recipe caveats go under the H1. Section caveats go directly under the
    `##` heading, before step 1.
- One callout per location. Do not stack them.

### Sentence structure

1. Imperative mood, verb first. `Chop onion (1)`, not
    `The onion should be chopped`.
1. Cut articles and filler. `Add garlic, cook until fragrant (30s)`, not
    `Add the garlic and cook it until it becomes fragrant, about 30 seconds`.
1. One action per line. Comma-chain only actions in the same vessel at the same
    moment: `Add onion (1) and salt (.25tsp)`.
1. Quantity in parentheses immediately after the thing it measures:
    `broth (32oz)`, not `32oz of broth`.
1. Time and temperature in parentheses at the end: `Roast (35-40m)`.
1. Verb-first fragments for actions; noun-first fragments for lists of things.
    Both drop the subject.
1. Target 80 characters. If a step runs long, promote it to a parent line plus a
    sublist rather than wrapping one sentence across lines.
1. No prose paragraphs in Prep or Cook. Caveats go in a `!!! note` callout under
    the heading.
1. Keep the source's measurements. Do not silently convert or rescale; if you do
    rescale, say so in a `!!! note` under the title.

### Quantities and units

- Decimals, never fractions: `.5c`, not `1/2 c`.
- No space between number and unit: `2tbsp`, `.25tsp`, `1.5lb`.
- Abbreviations: `c`, `tbsp`, `tsp`, `oz`, `lb`, `g`, `ml`, `s` seconds, `m`
    minutes, `h` hours.
- Never pluralise a unit: `2c` and `2lb`, never `2cs` or `2lbs`.
- Counts take no unit: `garlic (4)`, `eggs (2)`.
- Two useful forms go in one parenthetical, comma-separated:
    `broccoli (2lb, 3-4)`, `flour (2c, 258g)`.
- Oven temperature is a bare Fahrenheit number: `Oven to 400`.
- Ranges use a hyphen with no spaces: `7-10m`, `425-450`.

### Markdown mechanics

- Every ordered item is `1.`. Never `2.` or `3.` - numbering renders
    automatically, and `1.` keeps diffs clean when steps move.
- `-` for unordered lists.
- 4 spaces per indent level.
- `mdformat --wrap 80` runs in pre-commit and is the final authority on
    formatting. Where a norm here disagrees with what it emits, it wins - run it
    and keep its output.
- Wrap at 80 characters. Three things exceed it and are left alone: a citation
    URL, a callout body (`mdformat` wraps it to 80, then adds the 4-space
    indent), and a table row, which `mdformat` never splits.
- Links appear only in Citation.
- Blank line between a heading and its content. No trailing whitespace.

### Before you call it done

1. Section headings are exactly `Shopping`, `Prep`, `Cook`, `Notes`, `Citation`.
1. All four Shopping categories present and in order.
1. Every Shopping item appears in Prep or Cook, and vice versa.
1. Every quantity in Prep and Cook matches Shopping.
1. No fractions, no spaced units, no placeholder `X`.
1. No line over 80 characters except a citation URL, a callout body, or a table
    row.
1. Spell-check the title and the ingredient names.
1. `index.md` links to the file and names it the same way.
