#!/usr/bin/env python3
"""campfinder - find a dog-friendly waterfront campsite that is actually open.

    campfinder check --checkin 2026-09-04 --nights 2
    campfinder check --checkin 2026-09-04 --nights 2 --lakes-only
    campfinder watch --checkin 2026-09-04 --nights 2 --every 15m

Reads the real booking systems, not marketing pages:
  * ReserveAmerica legacy state storefronts (CT / MA / RI / NY) - server-rendered
    per-site, per-night matrices, no JavaScript and no bot wall.
  * recreation.gov's public JSON API for federal campgrounds.

Everything it prints is something it actually saw in a booking calendar.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import campspot
import newbook
import recreation_gov as rg
import water
import reserveamerica as ra

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = json.load(open(os.path.join(HERE, "registry.json")))
EXCLUDED = [s.upper() for s in REGISTRY["excluded_site_types"]]
LAKES = {(c["state"], c["park_id"]): c for c in REGISTRY["lake_campgrounds"]}


EXCLUDED_CAMPGROUNDS = ("HORSEMEN", "HORSE CAMP", "PICNIC", "SHELTER", "DAY USE",
                        "DAY PASS", "PAVILION", "BACKPACK", "RIVER CAMPING")


def is_real_campground(name):
    """Skip facilities that are not overnight drive-in campgrounds at all."""
    up = name.upper()
    return not any(x in up for x in EXCLUDED_CAMPGROUNDS)


def is_real_campsite(rec):
    """Filter out shelters, pavilions, group areas, cabins and horse-only sites.

    Raw availability counts are actively misleading without this: on Labor Day
    weekend 2026 the only "open" sites at Myles Standish were all HORSE
    REQUIRED, and every opening at Mount Greylock was a GROUP area.
    """
    blob = f"{rec.get('site_type','')} {rec.get('loop','')}".upper()
    if rec.get("site_type") and rec.get("site_type") == rec.get("loop"):
        return False
    return not any(bad in blob for bad in EXCLUDED)


def parse_every(s):
    m = re.fullmatch(r"(\d+)\s*([smh]?)", s.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(f"bad interval: {s}")
    n, unit = int(m.group(1)), m.group(2) or "m"
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


def nights_of(checkin, n):
    return [(checkin + dt.timedelta(days=i)).isoformat() for i in range(n)]


def scan(states, checkin, n_nights, lakes_only=False, verbose=True):
    """Return every real, bookable campsite open for the whole stay."""
    want = nights_of(checkin, n_nights)
    hits = []
    for st in states:
        try:
            client = ra.client(st)
            cgs = client.campgrounds()
        except Exception as exc:
            print(f"  ! {st}: directory unavailable ({exc})", file=sys.stderr)
            continue
        cgs = [c for c in cgs if is_real_campground(c["name"])]
        if st == "NY" and REGISTRY.get("ny_in_range_park_ids"):
            allowed = set(REGISTRY["ny_in_range_park_ids"])
            cgs = [c for c in cgs if c["park_id"] in allowed]
        if lakes_only:
            cgs = [c for c in cgs if (st, c["park_id"]) in LAKES]
        if verbose:
            print(f"  {st}: checking {len(cgs)} facilities", file=sys.stderr)
        for cg in cgs:
            meta = LAKES.get((st, cg["park_id"]), {})
            try:
                grid = client.matrix(cg["park_id"], checkin)
            except Exception as exc:
                print(f"    ! {cg['name']}: {exc}", file=sys.stderr)
                continue
            for site, rec in grid["sites"].items():
                if not all(rec["nights"].get(d) == "available" for d in want):
                    continue
                if not is_real_campsite(rec):
                    continue
                hits.append({
                    "state": st, "park_id": cg["park_id"],
                    "campground": cg["name"], "site": site,
                    "site_id": rec["site_id"], "loop": rec.get("loop", ""),
                    "site_type": rec.get("site_type", ""),
                    "nights": want,
                    "lake": meta.get("lake"),
                    "waterfront": meta.get("waterfront"),
                    "dogs": meta.get("dogs"),
                    "drive_hrs": meta.get("drive_hrs"),
                    "book_url": (
                        f"https://{ra.AGENCIES[st][0]}/campsiteDetails.do"
                        f"?contractCode={st}&parkId={cg['park_id']}"
                        f"&siteId={rec['site_id']}"
                        f"&arvdate={checkin.strftime('%m/%d/%Y')}"
                    ),
                })
            time.sleep(0.3)
    # Enrich only the hits: the detail page is one extra fetch per site, and
    # it is the only authoritative source for waterfront + pet status.
    for h in hits:
        try:
            d = ra.client(h["state"]).site_details(
                h["park_id"], h["site_id"], checkin)
        except Exception:
            d = {}
        h["detail"] = d
        h["pets_allowed"] = d.get("pets_allowed")
        h["max_pets"] = d.get("max_pets")
        h["waterfront_flag"] = d.get("waterfront")
        h["lat"], h["lng"] = d.get("lat"), d.get("lng")
        time.sleep(0.2)
    return hits


def scan_federal(checkin, n_nights, radius=130):
    out = []
    checkout = checkin + dt.timedelta(days=n_nights)
    for cg in rg.search_campgrounds(REGISTRY["home"]["lat"],
                                    REGISTRY["home"]["lng"], radius):
        res = rg.availability(cg["id"], checkin, checkout)
        for site, info in (res.get("sites") or {}).items():
            out.append({
                "state": cg["state"], "campground": cg["name"], "site": site,
                "loop": info.get("loop", ""), "site_type": info.get("type", ""),
                "nights": nights_of(checkin, n_nights),
                "distance_mi": cg["distance_mi"],
                "book_url": info["book_url"], "source": "recreation.gov",
            })
    return out


def apply_filters(hits, dogs=False, waterfront_only=False):
    """Drop anything that fails a hard requirement.

    `Pets Allowed` and `Waterfront Site` come from the reservation record
    itself. Where an agency omits the waterfront flag we keep the site but
    mark it unknown rather than silently claiming it is on the water.
    """
    out = []
    ct_ok = set(REGISTRY["pet_policy"]["CT"].get("dog_ok_park_ids") or [])
    banned = REGISTRY.get("pets_banned_park_ids", {})
    for h in hits:
        if dogs:
            pa = (h.get("pets_allowed") or "").strip().upper()
            if pa in ("N", "NO", "NONE"):
                continue
            # A blank Pets Allowed field is not permission. Enforce the
            # agency rules explicitly: CT bans pets at every state PARK
            # campground, and RI bans them at its two beach campgrounds -
            # both leave the field empty rather than setting it to "N".
            # These agency rules govern STATE campgrounds only. Private parks
            # carry no state park_id, so applying the rules to them drops
            # every dog-friendly private CT campground by accident.
            is_state = h.get("source") not in ("campspot", "newbook")
            if is_state:
                if h.get("park_id") in (banned.get(h.get("state")) or []):
                    continue
                if (h.get("state") == "CT" and ct_ok
                        and h.get("park_id") not in ct_ok):
                    continue
        if waterfront_only:
            flag = (h.get("waterfront_flag") or "").strip().upper()
            reg = h.get("waterfront")
            if flag == "N":
                continue
            if flag != "Y" and reg != "true_waterfront":
                continue
        out.append(h)
    return out


def scan_private(checkin, n_nights, radius=100, pets=1, log=None):
    """Campspot-hosted private campgrounds: site-level openings, priced."""
    checkout = checkin + dt.timedelta(days=n_nights)
    home = REGISTRY["home"]
    out = []
    for h in campspot.scan(home["lat"], home["lng"], checkin, checkout,
                           radius_miles=radius, pets=pets, log=log):
        out.append({
            "source": "campspot", "state": h["state"],
            "campground": h["campground"],
            "site": ", ".join(h["sites"][:12]) + (" ..." if len(h["sites"]) > 12 else ""),
            "site_type": h["site_type"], "loop": h["category"],
            "nights": nights_of(checkin, n_nights),
            "per_night": h["per_night"], "total": h["total"],
            "pets_allowed": "yes" if h["pets"] else None,
            "waterfront": {"on_water": "true_waterfront",
                           "near_water": "near_water"}.get(h["water"]),
            "water_note": ", ".join(h["park_water_features"]),
            "drive_hrs": round(h["miles"] / 45.0, 1),
            "miles": h["miles"], "phone": h["phone"],
            "book_url": h["book_url"],
        })
    # Newbook-hosted parks (Odetah) - listed in registry.json
    for nb in REGISTRY.get("newbook", []):
        try:
            cats = newbook.Client(nb["booking_url"]).availability(checkin, checkout)
        except Exception as exc:
            if log:
                log(f"    ! {nb['name']}: {exc}")
            continue
        for c in cats:
            if not c["available"]:
                continue
            if any(x in c["category"].upper() for x in ("CABIN", "YURT", "LODGE", "RENTAL")):
                continue
            out.append({
                "source": "newbook", "state": nb["state"],
                "campground": nb["name"], "site": c["category"],
                "site_type": c["category"], "loop": "",
                "nights": nights_of(checkin, n_nights),
                "per_night": c["from_price"],
                "total": (c["from_price"] or 0) * n_nights,
                "pets_allowed": nb.get("dogs"),
                "waterfront": nb.get("waterfront"), "water_note": nb.get("lake"),
                "drive_hrs": nb.get("drive_hrs"), "phone": nb.get("phone"),
                "book_url": nb["booking_url"],
            })
    return out


def annotate_water(hits, log=None):
    """Attach live cyanobacteria status to any hit whose lake VT monitors.

    A lake missing from the feed is UNMONITORED, not clean - we say so rather
    than implying a clean bill of health.
    """
    if not any(h.get("lake") for h in hits):
        return hits
    try:
        latest = water.vt_latest_by_lake()
    except Exception as exc:
        if log:
            log(f"  ! water-quality feed unavailable: {exc}")
        return hits
    for h in hits:
        rec = water.lookup(h.get("lake"), latest)
        if rec:
            h["water_status"] = f"{rec['status']} (reported {rec['date']})"
            h["water_alert"] = water.is_alert(rec)
    return hits


def rank(hits):
    order = {"true_waterfront": 0, "near_water": 1, None: 2}
    return sorted(hits, key=lambda h: (
        1 if h.get("water_alert") else 0,
        order.get(h.get("waterfront"), 2),
        h.get("drive_hrs") or 99,
        h["campground"], h["site"],
    ))


def render(hits, checkin, n_nights):
    checkout = checkin + dt.timedelta(days=n_nights)
    print(f"\n{'='*74}")
    print(f"  {checkin:%a %b %-d} -> {checkout:%a %b %-d, %Y}  ({n_nights} night"
          f"{'s' if n_nights != 1 else ''})")
    print(f"{'='*74}")
    if not hits:
        print("\n  Nothing open. Every real campsite in range is booked.\n")
        print("  Next moves:")
        print("   - run `campfinder watch` to catch cancellations")
        print("   - call the private lakefront campgrounds in registry.json;")
        print("     their booking engines are bot-walled, so a human has to ask")
        return
    for h in rank(hits):
        wf = {"true_waterfront": "ON THE WATER", "near_water": "near water"}.get(
            h.get("waterfront"), "")
        bits = [b for b in (h.get("lake"), wf,
                            f"{h['drive_hrs']}h from New Haven"
                            if h.get("drive_hrs") else None) if b]
        label = "sites" if "," in str(h["site"]) else "site"
        src = {"campspot": "  (private, Campspot)",
               "newbook": "  (private, Newbook)"}.get(h.get("source"), "")
        print(f"\n  {h['campground']} - {label} {h['site']}  [{h['state']}]{src}")
        if bits:
            print(f"     {' | '.join(bits)}")
        print(f"     {h.get('site_type') or '?'} | loop {h.get('loop') or '?'}")
        if h.get("per_night"):
            print(f"     ${h['per_night']:.0f}/night, ${h['total']:.0f} for the stay"
                  + (f" | {h['phone']}" if h.get("phone") else ""))
        if h.get("water_status"):
            flag = "  <-- ALGAE ALERT" if h.get("water_alert") else ""
            print(f"     water quality: {h['water_status']}{flag}")
        if h.get("water_note"):
            print(f"     park water features: {h['water_note']}")
        flag = (h.get("waterfront_flag") or "").strip().upper()
        if flag:
            print(f"     waterfront site (per booking record): "
                  f"{'YES' if flag == 'Y' else 'no'}")
        pets = h.get("pets_allowed")
        if pets:
            mx = f", max {h['max_pets']}" if h.get("max_pets") else ""
            print(f"     pets allowed (per booking record): {pets}{mx}")
        elif h.get("dogs"):
            print(f"     dogs: {h['dogs']}")
        if h.get("lat") and h.get("lng"):
            print(f"     map: https://maps.google.com/?q={h['lat']},{h['lng']}")
        print(f"     book: {h['book_url']}")
    print()


def cmd_check(args):
    checkin = dt.date.fromisoformat(args.checkin)
    states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    hits = []
    if not args.no_state:
        print(f"Scanning {', '.join(states)} state systems...", file=sys.stderr)
        hits = scan(states, checkin, args.nights, args.lakes_only)
    if args.federal:
        print("Scanning recreation.gov...", file=sys.stderr)
        hits += scan_federal(checkin, args.nights)
    if args.private:
        print("Scanning Campspot private campgrounds...", file=sys.stderr)
        hits += scan_private(checkin, args.nights, args.radius,
                             pets=1 if args.dogs else 0,
                             log=lambda m: print(m, file=sys.stderr))
    hits = annotate_water(hits, log=lambda m: print(m, file=sys.stderr))
    raw = len(hits)
    hits = apply_filters(hits, args.dogs, args.waterfront_only)
    if raw != len(hits):
        print(f"  filtered {raw} -> {len(hits)} after dog/waterfront rules",
              file=sys.stderr)
    render(hits, checkin, args.nights)
    if args.json:
        json.dump(hits, open(args.json, "w"), indent=1)
        print(f"  wrote {args.json}", file=sys.stderr)
    return hits


def cmd_watch(args):
    """Cancellation scanner: re-scan on an interval, announce anything new."""
    checkin = dt.date.fromisoformat(args.checkin)
    states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    seen = set()
    first = True
    while True:
        hits = [] if args.no_state else scan(
            states, checkin, args.nights, args.lakes_only, verbose=False)
        if args.private:
            hits += scan_private(checkin, args.nights, args.radius,
                                 pets=1 if args.dogs else 0)
        hits = apply_filters(hits, args.dogs, args.waterfront_only)
        key = lambda h: (h.get("source", "state"), h["state"],
                         h.get("park_id") or h["campground"], h["site"])
        fresh = [h for h in hits if key(h) not in seen]
        for h in hits:
            seen.add(key(h))
        stamp = time.strftime("%H:%M:%S")
        if fresh:
            print(f"\n[{stamp}] {len(fresh)} NEW opening(s)")
            render(fresh, checkin, args.nights)
            if args.notify:
                os.system(args.notify)
        else:
            print(f"[{stamp}] nothing new "
                  f"({len(hits)} open total)" + (" - baseline" if first else ""))
        first = False
        time.sleep(args.every)


def main():
    p = argparse.ArgumentParser(prog="campfinder", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--checkin", required=True, help="YYYY-MM-DD")
        sp.add_argument("--nights", type=int, default=2)
        sp.add_argument("--states", default="CT,MA,RI,NY,VT")
        sp.add_argument("--lakes-only", action="store_true",
                        help="only campgrounds on a lake (see registry.json)")
        sp.add_argument("--dogs", action="store_true",
                        help="drop sites whose booking record forbids pets")
        sp.add_argument("--waterfront-only", action="store_true",
                        help="only sites flagged waterfront in the booking "
                             "record (or known-waterfront in registry.json)")

    c = sub.add_parser("check", help="one-shot availability scan")
    common(c)
    c.add_argument("--federal", action="store_true",
                   help="also scan recreation.gov")
    c.add_argument("--private", action="store_true",
                   help="also scan Campspot-hosted private campgrounds")
    c.add_argument("--radius", type=int, default=100,
                   help="miles from home for private-campground search")
    c.add_argument("--no-state", action="store_true",
                   help="skip the state-park scan (private/federal only)")
    c.add_argument("--json", help="write raw results here")
    c.set_defaults(func=cmd_check)

    w = sub.add_parser("watch", help="poll for cancellations")
    common(w)
    w.add_argument("--every", type=parse_every, default=900,
                   help="interval, e.g. 15m, 600s, 1h")
    w.add_argument("--notify", help="shell command to run on a new opening")
    w.add_argument("--private", action="store_true",
                   help="also watch Campspot-hosted private campgrounds")
    w.add_argument("--radius", type=int, default=100)
    w.add_argument("--no-state", action="store_true")
    w.set_defaults(func=cmd_watch)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
