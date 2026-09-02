"""Campspot availability via the consumer app's own JSON API.

Campspot hosts most of the private lakefront campgrounds around New Haven
(Point Folly, Water's Edge, Chamberlain Lake, Prospect Mountain, ...). The
public site is behind DataDome, but the Angular app talks to a JSON backend
at https://www.campspot.com/api/gator-core that answers plain HTTP as long as
the request carries `X-Client-Type: CONSUMER`. Without that header every call
returns GATOR-41 "Unknown request".

Two calls do the work:

  /v2/availability/parks/map/{cLat}/{cLng}/{neLat}/{neLng}/{uLat}/{uLng}
      ?checkin=&checkout=&guests=          -> every park in a bounding box
                                              with AVAILABLE / UNAVAILABLE
  /v2/availability/parks/{parkId}
      ?checkin=&checkout=&guests=&includeUnavailable=true
                                           -> per site-type: campsites open,
                                              price, pet flag, failure reason

The `guests` value is `guests` + one count per park age-category, in the
park's own order, + pets. Parks disagree on the order (Point Folly lists
Children before Adults), so the adult count must go in whichever slot is
named "adult" - putting 2 in the first slot books two children and quietly
returns nothing available.
"""

import datetime as dt
import json
import math
import re
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
BASE = "https://www.campspot.com/api/gator-core"

# Site-type / feature words that mean the site itself touches the water,
# versus the campground merely having a beach somewhere.
ON_WATER = ("lakefront", "waterfront", "water front", "lakeside", "pondfront",
            "pond front", "riverfront", "beachfront", "on the lake",
            "on the water", "shoreline")
NEAR_WATER = ("lakeview", "lake view", "water view", "beach", "lake", "pond")

_park_cache = {}


def _get(path, timeout=60):
    req = urllib.request.Request(BASE + path, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "X-Client-Type": "CONSUMER",
        "Referer": "https://www.campspot.com/search",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def miles(lat1, lng1, lat2, lng2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def park(park_id):
    if park_id not in _park_cache:
        _park_cache[park_id] = _get(
            f"/v2/parks/id/{park_id}?useCustomParkData=true")["park"]
    return _park_cache[park_id]


def park_by_slug(slug):
    return _get(f"/v2/parks/slug/{slug}?useCustomParkData=true")["park"]


def guests_string(park_rec, adults=2, pets=1):
    """Build the `guests` param for this park's age categories."""
    cats = park_rec.get("ageCategories") or []
    counts = [0] * len(cats)
    if counts:
        idx = next((i for i, c in enumerate(cats)
                    if re.search(r"adult", c.get("name", ""), re.I)), 0)
        counts[idx] = adults
    return "guests" + "".join(f"{c}," for c in counts) + str(pets)


def search(lat, lng, checkin, checkout, radius_miles=100, pets=1):
    """Every Campspot park within `radius_miles` with its availability flag."""
    d = radius_miles / 69.0
    ne_lat, ne_lng = lat + d, lng + d / math.cos(math.radians(lat))
    q = urllib.parse.urlencode({
        "checkin": checkin.isoformat(), "checkout": checkout.isoformat(),
        "guests": f"guests2,0,{pets}",
    })
    parks = _get(f"/v2/availability/parks/map/{lat}/{lng}/{ne_lat}/{ne_lng}"
                 f"/{lat}/{lng}?{q}", timeout=120)
    out = []
    for p in parks:
        mi = miles(lat, lng, p["latitude"], p["longitude"])
        if mi > radius_miles:
            continue
        feats = [f["name"] for f in p.get("parkFeatures", [])]
        out.append({
            "id": p["id"], "slug": p.get("slug"), "name": p["displayName"],
            "city": p.get("city"), "state": p.get("state"),
            "lat": p["latitude"], "lng": p["longitude"], "miles": round(mi),
            "availability": p.get("availability"),
            "features": feats,
            "water_features": [f for f in feats if any(
                w in f.lower() for w in ("waterfront", "beach", "lake",
                                         "pond", "kayak", "canoe", "boat"))],
        })
    out.sort(key=lambda x: x["miles"])
    return out


def water_rating(text):
    t = text.lower()
    if any(w in t for w in ON_WATER):
        return "on_water"
    if any(w in t for w in NEAR_WATER):
        return "near_water"
    return ""


def availability(park_id, checkin, checkout, adults=2, pets=1):
    """Open site-types (with site names + price) for one park and stay."""
    p = park(park_id)
    q = urllib.parse.urlencode({
        "checkin": checkin.isoformat(), "checkout": checkout.isoformat(),
        "guests": guests_string(p, adults, pets),
        "useCustomParkData": "true", "includeUnavailable": "true",
    })
    types = _get(f"/v2/availability/parks/{park_id}?{q}")
    open_types, reasons = [], set()
    for t in types:
        if t.get("availability") == "AVAILABLE":
            open_types.append({
                "type": t.get("name"),
                "type_id": t.get("id"),
                "category": t.get("campsiteCategoryCode"),
                "sites": [s.get("name") for s in (t.get("campsites") or [])],
                "per_night": t.get("averagePricePerNight"),
                "total": t.get("totalTripPrice"),
                "pets": t.get("isPetFriendly"),
                "water": water_rating(t.get("name", "") + " "
                                      + (t.get("description") or "")),
            })
        else:
            for fr in t.get("failureReasons") or []:
                reasons.add(fr.get("reason", ""))
    return {
        "park": {
            "id": p["id"], "name": p.get("name"), "city": p.get("city"),
            "state": p.get("state"), "phone": p.get("phoneNumber"),
            "pets_allowed": p.get("petsAllowed"),
            "lat": p.get("latitude"), "lng": p.get("longitude"),
            "book_url": f"https://www.campspot.com/book/{p.get('slug')}",
        },
        "open": open_types,
        "reasons": sorted(reasons),
    }


def scan(lat, lng, checkin, checkout, radius_miles=100, pets=1,
         want_categories=("tent", "rv"), sleep=0.25, log=None,
         trust_search=False):
    """Discover parks in range, then pull site-level availability for each.

    The map search's AVAILABLE/UNAVAILABLE flag is computed with a generic
    `guests2,0,N` string, which is wrong for any park that lists Children
    before Adults - it books 2 children and 0 adults and comes back
    UNAVAILABLE. Windmill Hill, Water's Edge and Chamberlain Lake were all
    silently dropped that way while genuinely open. So by default we use the
    search only to DISCOVER parks and re-check every one of them with its own
    correctly-ordered guest string. `trust_search=True` restores the fast,
    lossy behaviour.
    """
    hits = []
    parks = search(lat, lng, checkin, checkout, radius_miles, pets)
    if log:
        flagged = sum(1 for p in parks if p['availability'] == 'AVAILABLE')
        log(f"  campspot: {len(parks)} parks in {radius_miles} mi "
            f"({flagged} flagged available by search; re-checking all)")
    for p in parks:
        if trust_search and p["availability"] != "AVAILABLE":
            continue
        try:
            av = availability(p["id"], checkin, checkout, pets=pets)
        except Exception as exc:
            if log:
                log(f"    ! {p['name']}: {exc}")
            continue
        # A park-level "Waterfront"/"Beach" feature only proves the campground
        # has water somewhere; it says nothing about the site. The site-type
        # name is the only site-level signal Campspot exposes, so a
        # park-level feature can at most earn "near_water".
        park_water = "near_water" if water_rating(" ".join(p["water_features"])) else ""
        for t in av["open"]:
            if want_categories and t["category"] not in want_categories:
                continue
            if pets and t["pets"] is False:
                continue
            hits.append({
                "source": "campspot",
                "campground": av["park"]["name"],
                "city": p["city"], "state": p["state"], "miles": p["miles"],
                "site_type": t["type"], "category": t["category"],
                "sites": t["sites"],
                "per_night": t["per_night"], "total": t["total"],
                "pets": t["pets"],
                "water": t["water"] or park_water,
                "park_water_features": p["water_features"],
                "phone": av["park"]["phone"],
                "book_url": av["park"]["book_url"],
            })
        time.sleep(sleep)
    order = {"on_water": 0, "near_water": 1, "": 2}
    hits.sort(key=lambda h: (order.get(h["water"], 2), h["miles"]))
    return hits
