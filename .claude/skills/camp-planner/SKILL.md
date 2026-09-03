---
name: camp-planner
description: Build a 5-tab camping trip planner (Itinerary, Packing List, Prep Schedule, Menu, Shopping List) as an .xlsx that imports cleanly into Google Sheets. The shopping list is derived from the menu, the prep schedule from the departure date, and the packing list adapts to a dog, water, hiking or cold weather. Use when asked to plan a camping trip, make a trip itinerary, build a packing or shopping list, or turn found campsites into a trip plan.
---

# camp-planner

Turns a trip description into the planner. Run from the repo root:

```bash
python3 plan.py trips/mytrip.json -o planner.xlsx
```

Then in Google Sheets: **File > Import > Upload > Replace spreadsheet**, and
select the `have` / `packed` / `Done?` columns and **Insert > Checkbox**.

## How to use it with someone

Don't hand them `template.json` and walk away. Write the trip file *for* them
from what they tell you, then run it. Copy `trips/template.json`, or crib the
shape from `trips/example-white-mountains.json`, which is a full real trip.

What you need to ask for, roughly in order of how much it changes the output:

1. **Dates and where** — becomes `trip`, `legs`, and the day grid.
2. **What they want to do each day** — becomes `days[].items`. Bullets, not
   prose. Include the practical ones: "book the summit road reservation today,
   10am, 2-day rolling window" is the kind of line that saves a trip.
3. **What they're eating** — becomes `menu` + `recipes`. This is the highest-
   leverage input, because the entire shopping list falls out of it.
4. **Dog? Swimming? Hiking? Cold?** — one flag each, each adds a packing
   section. `dog` takes a name and personalises the items.

## The part that matters

`recipes` drives the shopping list. Name a meal on a day in `menu`, define its
ingredients in `recipes`, and each ingredient is routed to a supermarket aisle
and tagged with every meal that needs it — so `Garlic` is one row reading
"Shrimp Scampi (Sat 8/22), T-bone Steak (Sun 8/23)", not three rows.

Two behaviours worth knowing:

- **A recipe the menu never names is still shopped for.** That's deliberate —
  it's how "Breakfasts (all days)" works, and how a spare/backup meal still
  gets its ingredients bought. Nothing is silently dropped.
- **Ingredients merge on a normalised name.** "Garlic cloves (4-5)" and
  "Garlic" become one row; "Unsalted butter (4-5 tbsp)" merges into "Butter".
  Quantities, parentheticals and form-words (ground, fresh, chopped, cloves,
  tbsp) are stripped for matching, but distinct things stay distinct — "Rice"
  and "Rice vinegar" do not merge. If something merges wrongly, rename it in
  `recipes`.

Aisle routing prefers the **longest** matching keyword, which is why "Red
pepper flakes" lands in SPICES rather than PRODUCE. Add an `aisles` map to the
trip file to override. Anything unrecognised goes to an `OTHER` section rather
than disappearing.

## Seeding from campfinder

If campfinder found the campsite, feed its results straight in:

```bash
python3 cli.py check --checkin 2026-09-04 --nights 3 --dogs --private --json hits.json
python3 plan.py trips/mytrip.json --from-campfinder hits.json
```

Each hit becomes a leg, with the site number and booking URL in the
Confirmation column marked **NOT YET BOOKED** — replace that with the real
confirmation number once it's reserved.

## Scope

This is standalone: it imports nothing from campfinder, and campfinder imports
nothing from it. `--from-campfinder` only reads a JSON file that
`cli.py --json` already writes. Needs `openpyxl`.
