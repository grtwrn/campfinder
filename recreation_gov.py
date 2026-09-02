"""recreation.gov availability.

The only booking platform in this region with a clean, unauthenticated JSON
API. Federal campgrounds near New Haven are sparse (Corps of Engineers lakes,
mostly) but when one is in range this is the fastest and most reliable check.
"""

import datetime as dt
import json
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

BASE = "https://www.recreation.gov"

# Statuses the API returns that mean "you can book this night".
OPEN = {"Available"}


def _get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": BASE + "/",
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


def search_campgrounds(lat, lng, radius_miles=120, size=50):
    """Nearby federal campgrounds, nearest first."""
    q = urllib.parse.urlencode({
        "fq": "entity_type:campground",
        "lat": lat, "lng": lng, "radius": radius_miles, "size": size,
    })
    data = _get(f"{BASE}/api/search?{q}")
    out = []
    for r in data.get("results", []):
        out.append({
            "id": r.get("entity_id"),
            "name": r.get("name"),
            "city": r.get("city"),
            "state": r.get("state_code"),
            "distance_mi": round(float(r.get("distance") or 0), 1),
            "lat": r.get("lat"), "lng": r.get("lng"),
            "url": f"{BASE}/camping/campgrounds/{r.get('entity_id')}",
        })
    out.sort(key=lambda x: x["distance_mi"])
    return out


def _months_spanning(checkin, checkout):
    """Every first-of-month the API needs to cover [checkin, checkout)."""
    months, cur = [], checkin.replace(day=1)
    end = checkout.replace(day=1)
    while cur <= end:
        months.append(cur)
        cur = (cur.replace(day=28) + dt.timedelta(days=7)).replace(day=1)
    return months


def availability(campground_id, checkin, checkout):
    """Sites bookable for every night in [checkin, checkout).

    Returns {site_label: {"site_id","loop","nights":{date:status}}} filtered to
    sites open on ALL requested nights - a site free Friday but taken Saturday
    is not a weekend site.
    """
    nights = []
    d = checkin
    while d < checkout:
        nights.append(d)
        d += dt.timedelta(days=1)

    merged = {}
    for m in _months_spanning(checkin, checkout):
        url = (f"{BASE}/api/camps/availability/campground/{campground_id}"
               f"/month?start_date={m.strftime('%Y-%m-%d')}T00%3A00%3A00.000Z")
        try:
            data = _get(url)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "sites": {}}
        for sid, site in (data.get("campsites") or {}).items():
            rec = merged.setdefault(sid, {
                "site_id": sid,
                "site": site.get("site"),
                "loop": site.get("loop"),
                "type": site.get("campsite_type"),
                "avail": {},
            })
            for k, v in (site.get("availabilities") or {}).items():
                rec["avail"][k[:10]] = v

    open_sites = {}
    for sid, rec in merged.items():
        statuses = [rec["avail"].get(n.strftime("%Y-%m-%d")) for n in nights]
        if statuses and all(s in OPEN for s in statuses):
            open_sites[rec["site"] or sid] = {
                "site_id": sid,
                "loop": rec["loop"],
                "type": rec["type"],
                "nights": {n.strftime("%Y-%m-%d"): "Available" for n in nights},
                "book_url": f"{BASE}/camping/campsites/{sid}",
            }
    return {"error": None, "sites": open_sites, "total_sites": len(merged)}
