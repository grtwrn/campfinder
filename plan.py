#!/usr/bin/env python3
"""camp-planner - turn a trip description into a 5-tab camping planner.

    python3 plan.py trips/example-white-mountains.json -o planner.xlsx
    python3 plan.py trips/mytrip.json --from-campfinder hits.json

Produces Itinerary / Packing List / Prep Schedule / Menu / Shopping List,
matching the layout of a planner that already works, so it imports cleanly
into Google Sheets (File > Import > Upload > Replace spreadsheet).

The point of this over a blank template is that three of the five tabs are
*derived*, not typed:

  * The shopping list falls out of the menu. Name a meal on a day, and every
    ingredient in its recipe lands in the right aisle with a "For" column
    saying which meals need it - so "Garlic" appears once, marked
    "Shrimp Scampi (Sat), T-bone Steak (Sun)", instead of three times.
  * The prep schedule is generated backwards from the departure date.
  * The packing list adds conditional sections - dog gear, water gear, cold
    layers - based on flags in the trip file.

This module is standalone. It imports nothing from campfinder and campfinder
imports nothing from it; `--from-campfinder` just reads a JSON file that
`cli.py --json` already writes.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("openpyxl is required:  pip install openpyxl")

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- styling --
DARK = "10221B"        # matches the portfolio/site green
BAND = "E8EFEA"
GREY = "F2F2F2"
TITLE = Font(bold=True, size=14, color="FFFFFF")
SECTION = Font(bold=True, size=11, color="FFFFFF")
HEAD = Font(bold=True, size=10)
BODY = Font(size=10)
NOTE = Font(size=9, italic=True, color="6B6B6B")
FILL_DARK = PatternFill("solid", fgColor=DARK)
FILL_BAND = PatternFill("solid", fgColor=BAND)
FILL_GREY = PatternFill("solid", fgColor=GREY)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center")
THIN = Side(style="thin", color="D9D9D9")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# Default aisle routing. A trip file can override or extend this with its own
# "aisles" map; anything unmatched lands in OTHER so nothing is silently lost.
AISLES = [
    ("PRODUCE", ["onion", "bell pepper", "garlic", "lemon", "lime", "parsley",
                 "corn", "carrot", "cabbage", "cucumber", "scallion", "celery",
                 "tomato", "basil", "potato", "mushroom", "lettuce", "spinach",
                 "avocado", "apple", "berry", "berries", "banana", "fruit",
                 "salad", "herb", "ginger", "cilantro", "kale", "zucchini"]),
    ("MEAT & SEAFOOD", ["lamb", "beef", "steak", "chicken", "pork", "sausage",
                        "bacon", "shrimp", "salmon", "tuna", "fish", "scallop",
                        "turkey", "hot dog", "burger"]),
    ("DAIRY & EGGS", ["egg", "butter", "milk", "cheese", "parmesan", "mozzarella",
                      "yogurt", "cream", "lebne", "labneh", "sour cream"]),
    ("BAKERY & BREAD", ["bread", "sourdough", "bun", "tortilla", "bagel",
                        "roll", "pita", "baguette"]),
    ("PANTRY & DRY GOODS", ["flour", "rice", "pasta", "noodle", "linguine",
                            "oat", "sugar", "baking", "nori", "bean", "lentil",
                            "cereal", "granola", "buckwheat", "cornmeal",
                            "broth", "stock", "canned", "chip"]),
    ("CONDIMENTS & SAUCES", ["oil", "vinegar", "syrup", "honey", "pesto",
                             "mayo", "mustard", "ketchup", "soy sauce", "wine",
                             "sauce", "jam", "peanut butter", "tahini"]),
    ("SPICES & SEASONINGS", ["salt", "pepper flakes", "black pepper", "cumin",
                             "coriander", "allspice", "paprika", "rosemary",
                             "thyme", "oregano", "cinnamon", "spice",
                             "seasoning", "bay leaf", "chili powder", "cayenne",
                             "curry", "turmeric", "nutmeg", "cardamom",
                             "garlic powder", "onion powder", "dill", "sage",
                             "chili flakes", "za'atar", "herbes", "saffron",
                             "fennel seed", "mustard seed", "peppercorn"]),
    ("SNACKS & DESSERTS", ["marshmallow", "marshmello", "graham", "chocolate",
                           "cookie", "candy", "snack", "miso", "ramen",
                           "popcorn", "pretzel", "s'more", "dessert"]),
    ("DRINKS & ICE", ["ice", "water", "beer", "seltzer", "juice", "coffee",
                      "tea", "hot chocolate", "soda"]),
]

# Packing sections added only when the trip asks for them.
CONDITIONAL_PACKING = {
    "dog": ("{dog} (dog)", [
        "{dog}'s food", "{dog}'s bowl", "{dog}'s bed / cot", "{dog}'s towel",
        "{dog}'s life jacket", "Leash", "Poop bags", "Wipes",
        "Rabies vaccination certificate (required at check-in)",
        "Any medications", "Tie-out / stake",
    ]),
    "water": ("Water & beach", [
        "Towels", "Swimsuits", "Flip flops", "Life jackets",
        "Paddle board / kayak", "Dry bag", "Water shoes",
    ]),
    "hiking": ("Hiking", [
        "Hiking shoes", "Day pack", "Trail map / offline maps",
        "Water bottles", "Headlamp", "Trekking poles", "Blister kit",
    ]),
    "cold": ("Cold weather", [
        "Layers", "Rain jacket", "Warm hat", "Gloves",
        "Extra blanket", "Hand warmers",
    ]),
}

BASE_PACKING = {
    "Sleeping": ["Tent", "Stakes & guylines", "Sleeping bag", "Sleeping pad",
                 "Pillow", "Sheets + pillow case", "Tarp / footprint"],
    "Hanging out": ["Camp chairs", "Canopy tent", "Canopy ground tarp",
                    "Hammock", "Bug spray", "Sunscreen", "Games", "Speaker",
                    "Book"],
    "Cooking": ["Cooler", "Ice", "Camp stove", "Fuel", "Camp table",
                "Cast-iron", "Pan with lid", "Oven mitt", "Cutting board",
                "Knife", "Rubber spatula", "Silverware", "Plates", "Bowls",
                "Cups", "Sponge", "Dish soap", "Paper towels", "Trash bags",
                "Aluminum foil", "Charcoal", "Fire starter", "Lighter",
                "Oil/butter", "Salt & pepper", "Coffee setup"],
    "Safety & hygiene": ["First aid kit", "Soap", "Hand sanitizer",
                         "Pocket knife", "Hatchet", "Mosquito candles",
                         "Portable charger", "Bright light / lantern",
                         "Headlamp", "Toiletries", "Towels", "Meds"],
    "Documents": ["Reservation confirmations", "ID", "Park pass",
                  "Cash for firewood"],
}

# The prep countdown, as offsets from departure day.
PREP = [
    (-2, ["Grocery list", "Start freezing ice packs / water bottles for the cooler"]),
    (-1, ["Laundry: clothes, sleeping bags, towels",
          "Get last groceries (fresh items, timed for departure)",
          "Pack the car with everything except the cooler/perishables",
          "Charge portable charger + lights",
          "Gas up car, check tire pressure",
          "Double-check the Packing List tab",
          "Precool the cooler tonight so it's cold before perishables go in"]),
    (0, ["Load fresh groceries + perishables into the precooled cooler",
         "Final walkthrough, load up, depart"]),
]


# ------------------------------------------------------------- utilities --
def d(s):
    return dt.date.fromisoformat(s)


def label(date):
    return f"{date:%a} {date.month}/{date.day}"


def daterange(a, b):
    out, cur = [], a
    while cur <= b:
        out.append(cur)
        cur += dt.timedelta(days=1)
    return out


# Words that describe a form or quantity rather than a different ingredient.
# Stripping them is what lets "Garlic cloves (4-5)" and "Garlic" become one
# shopping row instead of two, without also merging "Rice" into "Rice vinegar".
QUALIFIERS = {
    "unsalted", "salted", "fresh", "freshly", "ground", "whole", "large",
    "small", "medium", "cooked", "raw", "chopped", "minced", "sliced",
    "diced", "cloves", "clove", "leaves", "sprigs", "tbsp", "tsp", "cup",
    "cups", "lb", "lbs", "oz", "boneless", "skinless", "extra", "virgin",
}


def norm(ingredient):
    """Merge key: drop parentheticals, quantities and form-qualifiers."""
    s = re.sub(r"\([^)]*\)", " ", ingredient)          # "(4-5 tbsp)"
    s = re.sub(r"—.*$", " ", s)                        # trailing asides
    s = re.sub(r"[0-9]+([-/][0-9]+)?", " ", s)
    words = [w for w in re.findall(r"[a-z']+", s.lower())
             if w not in QUALIFIERS]
    return " ".join(words) or ingredient.lower().strip()


def aisle_for(ingredient, overrides):
    """Route to an aisle by the LONGEST matching keyword.

    First-match-wins gets this wrong: PRODUCE claims "pepper", so "Red pepper
    flakes" and "Black pepper" both land in produce. Preferring the longest
    match sends them to SPICES where they belong.
    """
    low = ingredient.lower()
    best, best_len = "OTHER", 0
    for name, keys in list((overrides or {}).items()) + AISLES:
        for kw in keys:
            k = kw.lower()
            if k in low and len(k) > best_len:
                best, best_len = name, len(k)
    return best


def build_shopping(trip):
    """Derive the shopping list from the menu + recipes.

    Returns {aisle: [(item, "For ...")]} - one row per ingredient, with every
    meal that needs it collected into the For column.
    """
    recipes = trip.get("recipes", {})
    usage, display, order, seen_meals = {}, {}, [], set()

    def add(ing, tag):
        key = norm(ing)
        if key not in usage:
            usage[key], display[key] = [], ing.strip()
            order.append(key)
        # keep the shortest spelling as the label ("Garlic", not "Garlic cloves")
        if len(ing.strip()) < len(display[key]):
            display[key] = ing.strip()
        if tag and tag not in usage[key]:
            usage[key].append(tag)

    for entry in trip.get("menu", []):
        day = label(d(entry["date"])) if entry.get("date") else ""
        for slot in ("breakfast", "lunch", "dinner"):
            meal = (entry.get(slot) or "").strip()
            if not meal or meal in ("-", "—"):
                continue
            seen_meals.add(meal)
            for ing in recipes.get(meal, []):
                add(ing, f"{meal} ({day})" if day else meal)

    # Recipes the menu never names - "Breakfasts (all days)" and the like -
    # still need shopping for. Dropping them silently is how you arrive at
    # camp with no eggs.
    for meal, ings in recipes.items():
        if meal not in seen_meals:
            for ing in ings:
                add(ing, meal)
    # Standing items that aren't tied to a specific meal.
    for extra in trip.get("extras", []):
        if isinstance(extra, dict):
            add(extra.get("item", ""), extra.get("for", ""))
        else:
            add(extra, "")

    grouped = {}
    for key in order:
        name = display[key]
        grouped.setdefault(aisle_for(name, trip.get("aisles")), []).append(
            (name, ", ".join(usage[key])))
    # Keep aisles in shopping-route order, with anything unmatched last.
    names = [n for n, _ in AISLES] + list(trip.get("aisles") or {}) + ["OTHER"]
    return {n: grouped[n] for n in dict.fromkeys(names) if n in grouped}


def build_packing(trip):
    sections = {k: list(v) for k, v in BASE_PACKING.items()}
    for extra in trip.get("packing_extra", {}).items():
        sections.setdefault(extra[0], []).extend(extra[1])
    dog = trip.get("dog")
    for flag, (title, items) in CONDITIONAL_PACKING.items():
        if flag == "dog":
            if not dog:
                continue
            name = dog if isinstance(dog, str) else dog.get("name", "Dog")
            sections[title.format(dog=name)] = [i.format(dog=name) for i in items]
        elif trip.get(flag):
            sections[title] = list(items)
    return sections


def build_prep(trip):
    dep = d(trip["trip"]["start"])
    rows, custom = [], trip.get("prep_extra", {})
    for offset, tasks in PREP:
        day = dep + dt.timedelta(days=offset)
        tag = {0: " (departure day)", -1: " (day before)"}.get(offset, "")
        block = list(tasks) + list(custom.get(str(offset), []))
        for i, t in enumerate(block):
            rows.append((f"{label(day)}{tag}" if i == 0 else "", t))
    return rows


# ------------------------------------------------------------- rendering --
def _title(ws, text, width):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=width)
    c = ws.cell(1, 1, text)
    c.font, c.fill, c.alignment = TITLE, FILL_DARK, CENTER
    ws.row_dimensions[1].height = 26


def _section(ws, row, text, width):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    c = ws.cell(row, 1, text)
    c.font, c.fill, c.alignment = SECTION, FILL_DARK, CENTER
    return row + 1


def _header(ws, row, cells):
    for i, v in enumerate(cells, 1):
        c = ws.cell(row, i, v)
        c.font, c.fill, c.border, c.alignment = HEAD, FILL_BAND, BOX, CENTER
    return row + 1


def _widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def sheet_itinerary(wb, trip):
    ws = wb.create_sheet("Itinerary")
    t = trip["trip"]
    span = f"{d(t['start']):%b %-d}–{d(t['end']):%-d, %Y}"
    _title(ws, f"{t['name']} — Itinerary ({span})", 6)

    r = _section(ws, 3, "TRIP LEGS", 6)
    r = _header(ws, r, ["Leg", "Dates", "Location", "Confirmation"])
    for i, leg in enumerate(trip.get("legs", []), 1):
        dates = f"{label(d(leg['start']))} – {label(d(leg['end']))}"
        for col, v in enumerate([leg.get("name", f"Leg {i}"), dates,
                                 leg.get("location", ""),
                                 leg.get("confirmation", "")], 1):
            c = ws.cell(r, col, v)
            c.font, c.border, c.alignment = BODY, BOX, WRAP
        r += 1

    r += 1
    r = _section(ws, r, "DAY-BY-DAY", 6)
    r = _header(ws, r, ["Day", "", "Activity", "Relax", "Commute", "Note"])
    by_date = {e["date"]: e for e in trip.get("days", [])}
    for day in daterange(d(t["start"]), d(t["end"])):
        entry = by_date.get(day.isoformat(), {})
        items = entry.get("items") or ["—"]
        for i, item in enumerate(items):
            ws.cell(r, 1, label(day) if i == 0 else "").font = (
                HEAD if i == 0 else BODY)
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
            c = ws.cell(r, 2, f"• {item}" if not item.startswith("•") else item)
            c.font, c.alignment = BODY, WRAP
            if entry.get("shade"):
                for col in range(1, 7):
                    ws.cell(r, col).fill = FILL_GREY
            r += 1
        r += 1

    for note in trip.get("unplaced", []):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        c = ws.cell(r, 1, f"Not yet placed: {note}")
        c.font, c.alignment = NOTE, WRAP
        r += 1
    _widths(ws, [16, 18, 26, 16, 16, 30])
    return ws


def sheet_packing(wb, trip):
    ws = wb.create_sheet("Packing List")
    _title(ws, f"{trip['trip']['name']} — Packing List", 5)
    r = 3
    r = _header(ws, r, ["Category", "Item", "", "have", "packed"])
    for section, items in build_packing(trip).items():
        c = ws.cell(r, 1, section)
        c.font, c.fill = HEAD, FILL_BAND
        r += 1
        for item in items:
            ws.cell(r, 2, item).font = BODY
            for col in (4, 5):
                cell = ws.cell(r, col, False)
                cell.alignment, cell.border = CENTER, BOX
            r += 1
        r += 1
    _widths(ws, [20, 40, 4, 10, 10])
    return ws


def sheet_prep(wb, trip):
    ws = wb.create_sheet("Prep Schedule")
    dep = d(trip["trip"]["start"])
    _title(ws, f"Prep Schedule — leading up to {label(dep)} departure", 3)
    r = _header(ws, 3, ["Date", "Done?", "Task"])
    for day, task in build_prep(trip):
        ws.cell(r, 1, day).font = HEAD if day else BODY
        c = ws.cell(r, 2, False)
        c.alignment, c.border = CENTER, BOX
        ws.cell(r, 3, f"• {task}").font = BODY
        ws.cell(r, 3).alignment = WRAP
        r += 1
    _widths(ws, [24, 10, 76])
    return ws


def sheet_menu(wb, trip):
    ws = wb.create_sheet("Menu")
    _title(ws, "MENU", 4)
    r = _header(ws, 3, ["Day", "Breakfast", "Lunch", "Dinner"])
    for e in trip.get("menu", []):
        ws.cell(r, 1, label(d(e["date"])) if e.get("date") else "").font = HEAD
        for col, slot in enumerate(("breakfast", "lunch", "dinner"), 2):
            c = ws.cell(r, col, e.get(slot, ""))
            c.font, c.alignment, c.border = BODY, WRAP, BOX
        r += 1
    for note in trip.get("menu_notes", []):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        ws.cell(r, 1, note).font = NOTE
        r += 1

    r += 1
    r = _header(ws, r, ["Meal", "Ingredient", "", ""])
    for meal, ings in trip.get("recipes", {}).items():
        for i, ing in enumerate(ings):
            ws.cell(r, 1, meal if i == 0 else "").font = HEAD if i == 0 else BODY
            ws.cell(r, 2, ing).font = BODY
            r += 1
        r += 1
    _widths(ws, [26, 34, 34, 34])
    return ws


def sheet_shopping(wb, trip):
    ws = wb.create_sheet("Shopping List")
    _title(ws, "SHOPPING LIST", 4)
    r = _header(ws, 3, ["Item", "For", "Have", "Packed"])
    total = 0
    for aisle, rows in build_shopping(trip).items():
        r = _section(ws, r, aisle, 4)
        for item, why in rows:
            ws.cell(r, 1, item).font = BODY
            c = ws.cell(r, 2, why)
            c.font, c.alignment = BODY, WRAP
            for col in (3, 4):
                cell = ws.cell(r, col, False)
                cell.alignment, cell.border = CENTER, BOX
            r += 1
            total += 1
    _widths(ws, [30, 58, 10, 10])
    return ws, total


# ------------------------------------------------------------- campfinder --
def legs_from_campfinder(path, nights):
    """Seed itinerary legs from a `cli.py --json` result file."""
    hits = json.load(open(path))
    legs = []
    for h in hits[:4]:
        nights_list = h.get("nights") or []
        if not nights_list:
            continue
        start = nights_list[0]
        end = (d(nights_list[-1]) + dt.timedelta(days=1)).isoformat()
        where = h["campground"]
        if h.get("lake"):
            where += f" ({h['lake']})"
        site = h.get("site")
        legs.append({
            "name": f"Leg {len(legs) + 1}",
            "start": start, "end": end,
            "location": where,
            "confirmation": f"site {site} — NOT YET BOOKED: {h.get('book_url','')}",
        })
    return legs


# -------------------------------------------------------------------- cli --
def main():
    p = argparse.ArgumentParser(
        prog="plan.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("trip", help="path to a trip JSON file")
    p.add_argument("-o", "--out", help="output .xlsx (default: <trip>.xlsx)")
    p.add_argument("--from-campfinder", metavar="HITS.JSON",
                   help="seed itinerary legs from a campfinder --json result")
    args = p.parse_args()

    trip = json.load(open(args.trip))
    if args.from_campfinder:
        seeded = legs_from_campfinder(args.from_campfinder, None)
        if seeded:
            trip.setdefault("legs", [])
            trip["legs"] = seeded + trip["legs"]
            print(f"seeded {len(seeded)} leg(s) from {args.from_campfinder}")

    wb = Workbook()
    wb.remove(wb.active)
    sheet_itinerary(wb, trip)
    sheet_packing(wb, trip)
    sheet_prep(wb, trip)
    sheet_menu(wb, trip)
    _, n_items = sheet_shopping(wb, trip)

    out = args.out or os.path.splitext(args.trip)[0] + ".xlsx"
    wb.save(out)
    days = len(daterange(d(trip["trip"]["start"]), d(trip["trip"]["end"])))
    print(f"wrote {out}")
    print(f"  {days} days, {len(trip.get('legs', []))} leg(s), "
          f"{len(trip.get('recipes', {}))} recipes -> {n_items} shopping items")
    print("  import to Google Sheets: File > Import > Upload > Replace spreadsheet")
    print("  then select the have/packed/Done? columns and Insert > Checkbox")


if __name__ == "__main__":
    main()
