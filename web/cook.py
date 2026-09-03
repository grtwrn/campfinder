"""Parse a recipe book into {name: [ingredients]}.

People keep recipes in whatever shape they already have them, so this accepts
markdown, plain text, JSON and CSV and works out which is which. The output
feeds straight into the same `recipes` block that plan.py consumes, so an
imported cookbook and a hand-written trip file are indistinguishable
downstream.

The one judgement call that matters: real recipes carry a method as well as an
ingredient list, and a method line swept into the ingredients becomes a
grocery item that reads "Heat the oil in a large skillet". So parsing stops
collecting at any Directions/Method/Steps heading, and drops lines that look
like prose rather than ingredients.
"""

import csv
import io
import json
import re

# Headings that mean "ingredients follow" - skipped, not treated as a recipe.
INGREDIENT_HEADINGS = {
    "ingredients", "ingredient", "you will need", "shopping list", "what you need",
}
# Headings that mean the ingredient list is over.
METHOD_HEADINGS = {
    "directions", "direction", "instructions", "instruction", "method",
    "steps", "preparation", "prep", "notes", "note", "to serve", "serving",
    "equipment", "tips", "tip",
}
# Metadata lines to drop entirely.
META = re.compile(
    r"^\s*(serves?|servings?|yield|prep(aration)?\s*time|cook(ing)?\s*time|"
    r"total\s*time|difficulty|makes|calories|course|cuisine)\b[:\s]",
    re.I)

BULLET = re.compile(r"^\s*(?:[-*•▪·–—]|\d+[.)])\s+")
MD_HEAD = re.compile(r"^\s*(#{1,6})\s*(.+?)\s*#*\s*$")


def _clean(line):
    return BULLET.sub("", line).strip(" \t-–—•*")


def _is_method_heading(text):
    return text.strip().rstrip(":").strip().lower() in METHOD_HEADINGS


def _is_ingredient_heading(text):
    return text.strip().rstrip(":").strip().lower() in INGREDIENT_HEADINGS


def _looks_like_prose(text):
    """Method sentences masquerading as ingredients.

    An ingredient is a noun phrase: short, few words, rarely punctuated with
    sentence-enders. A step is a sentence, usually imperative.
    """
    t = text.strip()
    if len(t) > 90 or t.count(" ") > 12:
        return True
    if re.search(r"[.!?]\s+[A-Z]", t):          # two sentences
        return True
    if re.match(r"^(heat|add|stir|cook|bake|mix|combine|place|remove|pour|"
                r"season|serve|preheat|whisk|bring|reduce|simmer|transfer|let|"
                r"return|sprinkle|garnish|meanwhile|once|when|while|repeat|"
                r"continue|set aside|toss|fold|drain|rinse|cover|arrange)\b",
                t, re.I):
        return True
    return False


def _looks_like_title(line, nxt):
    """A bare line that introduces a recipe rather than being an ingredient."""
    t = line.strip()
    if not t or BULLET.match(line):
        return False
    if t.endswith(":"):
        return True
    if len(t) > 60 or _looks_like_prose(t):
        return False
    # Title Case or ALL CAPS, followed by something that looks like a list.
    titleish = t == t.upper() or re.match(r"^[A-Z0-9]", t)
    return bool(titleish and nxt is not None
                and (BULLET.match(nxt) or _is_ingredient_heading(nxt)))


def parse_text(text):
    """Markdown or plain text -> {recipe: [ingredients]} preserving order."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, current, collecting = {}, None, True

    def start(name):
        nonlocal current, collecting
        name = name.strip().rstrip(":").strip()
        if not name:
            return
        current, collecting = name, True
        out.setdefault(current, [])

    for i, raw in enumerate(lines):
        line = raw.rstrip()
        nxt = next((l for l in lines[i + 1:] if l.strip()), None)

        md = MD_HEAD.match(line)
        if md:
            text_ = md.group(2)
            if _is_method_heading(text_):
                collecting = False
            elif _is_ingredient_heading(text_):
                collecting = True
            else:
                start(text_)
            continue

        if not line.strip():
            continue
        if META.match(line):
            continue
        if _is_method_heading(line):
            collecting = False
            continue
        if _is_ingredient_heading(line):
            collecting = True
            continue
        if _looks_like_title(line, nxt):
            start(line)
            continue
        if current and collecting:
            item = _clean(line)
            if item and not _looks_like_prose(item):
                if item not in out[current]:
                    out[current].append(item)

    return {k: v for k, v in out.items() if v}


def parse_json(text):
    data = json.loads(text)
    out = {}
    if isinstance(data, dict):
        # {"recipes": {...}} or a bare {name: [ings]}
        data = data.get("recipes", data)
        for name, ings in data.items():
            if isinstance(ings, list):
                out[str(name)] = [str(i).strip() for i in ings if str(i).strip()]
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("title") or item.get("recipe")
            ings = item.get("ingredients") or item.get("items") or []
            if name and isinstance(ings, list):
                out[str(name)] = [str(i).strip() for i in ings if str(i).strip()]
    return out


def parse_csv(text):
    """Two-column recipe,ingredient - one row per ingredient."""
    out = {}
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return out
    start = 0
    head = [c.strip().lower() for c in rows[0][:2]]
    if head and head[0] in ("recipe", "meal", "name", "dish"):
        start = 1
    for row in rows[start:]:
        if len(row) < 2:
            continue
        name, ing = row[0].strip(), row[1].strip()
        if name and ing:
            out.setdefault(name, [])
            if ing not in out[name]:
                out[name].append(ing)
    return out


def parse(text, filename=""):
    """Sniff the format and parse. Returns {name: [ingredients]}."""
    text = (text or "").strip()
    if not text:
        return {}
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "json" or text[:1] in "[{":
        try:
            got = parse_json(text)
            if got:
                return got
        except Exception:
            pass
    if ext == "csv" or (ext == "" and text.count(",") > text.count("\n")):
        try:
            got = parse_csv(text)
            if got:
                return got
        except Exception:
            pass
    return parse_text(text)
