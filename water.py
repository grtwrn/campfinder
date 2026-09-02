"""Live cyanobacteria (blue-green algae) status for candidate lakes.

Early September is peak bloom season in New England, so "clean lake" is a
question you have to ask per-trip, not once. Vermont publishes the only
machine-readable live feed of the four states: the Health Department's
Cyanobacteria Tracker is an ArcGIS Experience app backed by a public feature
service, which answers plain HTTP.

    https://services.arcgis.com/YKJ5JtnaPQ2jDbX8/arcgis/rest/services
        /Cyanobacteria_All_Reports_view/FeatureServer/0/query

Statuses seen in `StatusWeb`: "Generally Safe", "Low Alert", "High Alert".

CT, MA and RI publish advisories as human-readable pages only; their URLs are
in ADVISORY_PAGES for a manual check. A lake absent from the feed is
*unmonitored*, which is not the same as clean.
"""

import datetime as dt
import json
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

VT_SERVICE = ("https://services.arcgis.com/YKJ5JtnaPQ2jDbX8/arcgis/rest/"
              "services/Cyanobacteria_All_Reports_view/FeatureServer/0/query")

ADVISORY_PAGES = {
    "CT": "https://portal.ct.gov/DEEP/Water/Water-Quality/Blue-Green-Algae-Blooms",
    "MA": "https://www.mass.gov/alerts/harmful-cyanobacterial-bloom-advisories-in-massachusetts",
    "RI": "https://dem.ri.gov/environmental-protection-bureau/water-resources/quality/beach-water-quality",
    "NY": "https://on.ny.gov/harmfulalgalblooms",
    "Bantam Lake, CT": "https://www.bantamlakect.com/cyano-stoplight",
}

BAD = ("alert", "warning", "advisory", "closed")


def vt_reports(since_days=21, timeout=90):
    """Every VT cyanobacteria report in the window, newest first."""
    since = (dt.date.today() - dt.timedelta(days=since_days)).isoformat()
    q = urllib.parse.urlencode({
        "where": f"ReportDate > TIMESTAMP '{since} 00:00:00'",
        "outFields": "ReportDate,Waterbody,Municipality,SiteName,StatusWeb",
        "orderByFields": "ReportDate DESC",
        "resultRecordCount": 2000,
        "f": "json",
    })
    req = urllib.request.Request(f"{VT_SERVICE}?{q}",
                                 headers={"User-Agent": UA,
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    out = []
    for f in data.get("features", []):
        a = f["attributes"]
        ms = a.get("ReportDate")
        out.append({
            "date": (dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
                     .date().isoformat() if ms else None),
            "waterbody": (a.get("Waterbody") or "").strip(),
            "site": a.get("SiteName"),
            "town": a.get("Municipality"),
            "status": a.get("StatusWeb"),
        })
    return out


def vt_latest_by_lake(since_days=21):
    """Most recent report per waterbody."""
    latest = {}
    for r in vt_reports(since_days):
        wb = r["waterbody"].lower()
        if not wb:
            continue
        if wb not in latest or (r["date"] or "") > (latest[wb]["date"] or ""):
            latest[wb] = r
    return latest


def lookup(lake_name, latest):
    """Match a registry lake name against the feed (substring, both ways)."""
    if not lake_name:
        return None
    n = lake_name.lower()
    for wb, rec in latest.items():
        if wb in n or n in wb:
            return rec
        # "Adams Reservoir" vs feed's "Adams Reservoir"; also bare first word
        head = n.split("(")[0].strip()
        if head and (head in wb or wb in head):
            return rec
    return None


def is_alert(rec):
    return bool(rec) and any(b in (rec.get("status") or "").lower() for b in BAD)
