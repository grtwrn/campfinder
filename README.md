# campfinder

Find a **dog-friendly waterfront campsite that is actually bookable** — across
Connecticut, Massachusetts, Rhode Island, New York and Vermont state parks,
Campspot- and Newbook-hosted private campgrounds, and recreation.gov.

It reads the real reservation systems rather than marketing pages, so
everything it prints is something it saw in a live booking calendar.

```bash
python3 cli.py check --checkin 2026-09-04 --nights 3 --dogs --private
```

```
  Windmill Hill - Litchfield, CT - sites 01, 03, 05, 06, 09, 13, 15  [CT]  (private, Campspot)
     near water | 0.7h from New Haven
     Tent Site | loop tent
     $18/night, $53 for the stay | 8605670089
     pets allowed (per booking record): yes
     book: https://www.campspot.com/book/windmill-hill
```

---

## Please read this before pointing it at anything

This is a **personal-use tool**, published so you can run it yourself for your
own trip. It is not a hosted service and should not be turned into one.

The booking sites it reads have terms that restrict automated access.
Aspira/ReserveAmerica prohibits using "any robot, bot, spider… or other manual
or automatic device or process to retrieve, index, data mine, scrape" its
sites, grants access "solely for your personal, non-commercial use," and sets
liquidated damages beyond 1,000 pages per 24 hours — a single full five-state
sweep is roughly 1,200–1,500 page loads, so **don't run the unfiltered sweep
casually**; use `--lakes-only` or `--states`. Campspot separately prohibits
scraping, copying its API, and reusing site data "even if not monetized."

Running this occasionally, from your own machine, to plan your own camping
trip is the intended use. Running it as a public service, at volume, or from a
datacenter is not — it would breach those terms, it would get blocked quickly
(AWS WAF and DataDome score datacenter IPs aggressively), and wrong
availability data sends real people on real drives. Decide for yourself where
your use falls.

No credentials, tokens or API keys are included in this repository.

---

## Install

```bash
git clone https://github.com/grtwrn/campfinder.git
cd campfinder
python3 --version        # 3.9+
pip install -r requirements.txt   # only needed for the optional browser fallback
python3 cli.py check --checkin 2026-09-04 --nights 2 --dogs --lakes-only
```

Everything except `browser.py` runs on the standard library alone.

Optional convenience:

```bash
alias campfinder="$PWD/campfinder"
```

### As a Claude Code skill

The repo ships a skill at `.claude/skills/campfinder/SKILL.md`. Clone it into a
project and Claude Code picks it up, or install it for every project:

```bash
mkdir -p ~/.claude/skills
cp -r .claude/skills/campfinder ~/.claude/skills/
```

Then ask Claude things like *"is anything open on Lake Waramaug this weekend
that takes a dog?"* and it will run the tool rather than guessing.

### Make it yours

Set `home` in `registry.json` to your own coordinates. Coverage is strongest
in southern New England; the registry's lake list, waterfront ratings, drive
times and pet rules are all data you can edit without touching code.

---

## Use

```bash
cd campfinder

# What is open this weekend, dogs allowed, anywhere in CT/MA/RI/NY?
python3 cli.py check --checkin 2026-09-04 --nights 2 --dogs

# Only lakes, only sites the booking record flags as waterfront
python3 cli.py check --checkin 2026-09-04 --nights 2 --dogs --lakes-only --waterfront-only

# Everything sold out? Watch for cancellations and shout when one lands.
python3 cli.py watch --checkin 2026-09-04 --nights 2 --dogs --every 15m \
    --notify 'notify-send "campsite open!"'
```

`--lakes-only` restricts to the curated lake campgrounds in `registry.json`
(fast: ~16 facilities). Without it the scan covers all ~254 state facilities and
takes 20-30 minutes.

## How it gets the data

**ReserveAmerica / Aspira (CT, MA, RI, NY state parks + NY DEC).**
The modern `www.reserveamerica.com` React app pulls availability over XHR from
`api.reserveamerica.com`, which sits behind an AWS WAF and answers scripted
clients with a "Human Verification" page. Every state agency, though, also runs
a **legacy server-rendered storefront** on its own subdomain, and that one
renders the complete per-site / per-night matrix directly into the HTML — no
JavaScript, no bot wall:

| State | Storefront | Contract |
|-------|------------|----------|
| CT | `connecticutstateparks.reserveamerica.com` | `CT` |
| MA | `massdcrcamping.reserveamerica.com` | `MA` |
| RI | `rhodeislandstateparks.reserveamerica.com` | `RI` |
| NY | `newyorkstateparks.reserveamerica.com` | `NY` |

The endpoint that matters:

```
/campsiteCalendar.do?page=matrix&contractCode=CT&parkId=100110&calarvdate=09/04/2026
```

`calarvdate` is the parameter that works (`arvdate` and `lengthOfStay` are
silently ignored). The response is a 2-week grid of `<div class='td status a'>`
cells — `a` available, `r` reserved, `x` not available.

**recreation.gov** has a clean public JSON API and needs none of this:
`/api/camps/availability/campground/{id}/month?start_date=...`.

## Three traps it handles

1. **Session bleed.** The storefront keeps the previous query's site-type filter
   in server-side session state. Reuse a session across parks and you silently
   get a filtered subset — 14 rows where the campground has 89 — with no error.
   Every query starts a fresh session.

2. **Pagination.** The site list is 25 rows per page. Reading only page 1 hides
   most of a big campground's sites, and therefore most of its openings.

3. **Fake openings.** Raw availability counts are actively misleading. On Labor
   Day weekend 2026, Myles Standish SF showed 12 "available" sites that were all
   flagged `HORSE REQUIRED TO CAMP`; every Mount Greylock opening was a `GROUP
   CAMPING` area; Rutland and Pulaski's were day-use `SHELTERS/PAVILIONS`.
   `is_real_campsite()` filters these out — see `excluded_site_types` in
   `registry.json`.

## Ground truth for the two questions that decide the trip

`campsiteDetails.do` exposes per-site fields the listing pages don't:

```
Pets Allowed: Domestic    Maximum Number of Pets: 2
Waterfront Site: N        GIS Details Latitude: 41.377...
```

`--dogs` and `--waterfront-only` filter on these. Where an agency omits the
waterfront flag (MA does), a site is kept but marked unknown rather than
claimed as waterfront.

## Pet rules worth knowing before you plan

- **Connecticut bans pets in every state PARK campground.** Dogs are legal only
  at three state FOREST campgrounds: American Legion (Hawes), Pachaug (Green
  Falls, Mount Misery), and Salt Rock. Of those, only Green Falls is on still
  water — and its booking records show no waterfront sites.
- **Massachusetts** allows dogs at most DCR campgrounds, but a paper rabies
  certificate is required at check-in.
- **New York** allows 2 pets in designated loops; paper rabies certificate
  required (metal tags are not accepted).

## What it does not cover

Private campgrounds — which is where most genuinely *on-the-water* dog-friendly
sites near New Haven are (Point Folly on Bantam Lake, Laurel Lock on Gardner
Lake, Witch Meadow, Wilderness Lake, Odetah). They run on Campspot, RoverPass,
Firefly and CampLife, all of which sit behind DataDome or equivalent and expose
no availability to scripted clients. `registry.json` carries their names, lakes,
waterfront status, dog policies and **phone numbers** — for these, calling is
still the fastest path.

`browser.py` is a headless-Chromium CDP driver kept for that purpose. It works,
but it is slow on a Raspberry Pi and unreliable under load; treat it as a
fallback, not the main path.

## Files

| File | Purpose |
|------|---------|
| `cli.py` | `check` / `watch` commands, filtering, output |
| `reserveamerica.py` | legacy storefront client, matrix parser, site details |
| `recreation_gov.py` | federal JSON API client |
| `registry.json` | lakes, waterfront status, pet rules, private-park phone list |
| `sweep.py` | bulk multi-state audit (writes JSON) |
| `browser.py` | headless Chromium over CDP (fallback for JS-only sites) |
| `plan.py` | trip planner — builds the 5-tab .xlsx (standalone) |
| `trips/` | trip files: `template.json` and a full worked example |

## Private campgrounds (added)

| Engine | Parks near New Haven | Status |
|---|---|---|
| **Campspot** (`campspot.py`) | Point Folly, Water's Edge, Chamberlain Lake, Windmill Hill, Prospect Mountain, Hopeville Hideaway, Strawberry Park, Lone Oak, White Pines, Sun Outdoors Mystic, ~70 more in 100 mi | **Automated.** `www.campspot.com/api/gator-core` with `X-Client-Type: CONSUMER`. Map search discovers parks; per-park call returns open site names + price + pet flag. `guests` = one count per age category in the *park's* order + pets - put adults in the slot named "adult". |
| **Newbook** (`newbook.py`) | Odetah | **Automated.** POST `availability_chart_responsive` to the instance's `generated_api_files/<hash>.php`. |
| Hipcamp | Quentins Treehouse, Sylvan Lake, Beach Pond | Not solved - GraphQL 404s to scripted calls; listing HTML ignores dates. |
| none (phone) | Witch Meadow, Laurel Lock, Wilderness Lake, Markham Meadows, Laurel Ridge, Wilcox | `registry.json` → `phone_only`. |

```bash
python3 cli.py check --checkin 2026-09-04 --nights 3 --dogs --private          # state + private
python3 cli.py check --checkin 2026-09-04 --nights 3 --dogs --private --no-state --radius 70
python3 cli.py watch --checkin 2026-09-04 --nights 2 --dogs --private --every 15m
```

**Holiday rule:** most private CT parks enforce a 3-night minimum on Labor Day / Memorial Day / July 4. A 2-night Fri→Sun query returns nothing while Fri→Mon is wide open. Query both.

## Trip planner (`plan.py`)

A separate, additive tool: turns a trip description into a 5-tab planner —
**Itinerary · Packing List · Prep Schedule · Menu · Shopping List** — as an
`.xlsx` that imports straight into Google Sheets.

```bash
pip install openpyxl
python3 plan.py trips/example-white-mountains.json -o planner.xlsx
```

Then in Sheets: *File > Import > Upload > Replace spreadsheet*, and select the
`have` / `packed` / `Done?` columns and *Insert > Checkbox*.

Three of the five tabs are **derived rather than typed**:

- **Shopping list from the menu.** Name a meal on a day, define its
  ingredients once, and each one is routed to a supermarket aisle and tagged
  with every meal that needs it — `Garlic` becomes a single row reading
  "Shrimp Scampi (Sat 8/22), T-bone Steak (Sun 8/23), Cold sesame noodle
  salad" instead of three separate rows.
- **Prep schedule from the departure date**, counted backwards.
- **Packing list from flags** — `dog` (personalised by name, and it remembers
  the rabies certificate), `water`, `hiking`, `cold`.

Details that stop it producing a subtly wrong list:

| Behaviour | Why |
|---|---|
| Recipes the menu never names are still shopped for | how "Breakfasts (all days)" and spare meals work — arriving with no eggs is the failure mode |
| Ingredients merge on a normalised name | "Garlic cloves (4-5)" → `Garlic`, "Unsalted butter (4-5 tbsp)" → `Butter`; but "Rice" and "Rice vinegar" stay separate |
| Aisle routing prefers the longest keyword match | otherwise PRODUCE claims "pepper" and "Red pepper flakes" ends up next to the onions |
| Unrecognised items go to an `OTHER` aisle | nothing is silently dropped |

Seed the itinerary from a campfinder search:

```bash
python3 cli.py check --checkin 2026-09-04 --nights 3 --dogs --private --json hits.json
python3 plan.py trips/mytrip.json --from-campfinder hits.json
```

Each hit becomes a leg with its site number and booking URL, marked
**NOT YET BOOKED**.

`plan.py` imports nothing from campfinder and campfinder imports nothing from
it — `--from-campfinder` just reads the JSON `cli.py --json` already writes.
Start from `trips/template.json`, or `trips/example-white-mountains.json` for a
full worked trip. Ships a Claude Code skill at `.claude/skills/camp-planner/`.


## Vermont

Vermont runs the same Aspira/ReserveAmerica storefront on **its own domain** —
`vtstateparks-visit.com`, `contractCode=VT` — so the existing client works
unchanged once the host is registered. 49 facilities.

```bash
python3 cli.py check --checkin 2026-09-04 --nights 2 --dogs --states VT
```

VT allows pets at tent/RV and lean-to sites (rabies certificate required; not
in cabins/cottages). Its site records confirm `Pets Allowed: Domestic`, but —
unlike RI — carry **no waterfront flag**, so waterfront ratings for VT parks in
`registry.json` come from park knowledge, not the booking record.

Nothing in Vermont is under about 2.5 h from New Haven.

## Water quality (`water.py`)

"Clean lake" is a per-trip question in September. Vermont publishes the only
machine-readable live feed of the five states — the Health Department's
Cyanobacteria Tracker, an ArcGIS Experience app over a public feature service:

```
services.arcgis.com/YKJ5JtnaPQ2jDbX8/arcgis/rest/services
  /Cyanobacteria_All_Reports_view/FeatureServer/0/query
```

`cli.py` annotates every hit whose lake appears in the feed and **sorts lakes
under an alert to the bottom**. This is not cosmetic: on 2026-09-02 the biggest
block of open VT sites was 44 sites at Lake Carmi, which was under a **Low
Alert** dated 2026-08-31, while Woodford's Adams Reservoir read *Generally
Safe*.

A lake absent from the feed is **unmonitored, not clean**. CT/MA/RI/NY publish
advisories as human-readable pages only — see `ADVISORY_PAGES` in `water.py`.
