"""ReserveAmerica (Aspira) availability via the legacy per-state subdomains.

The modern www.reserveamerica.com React app loads availability over XHR from
api.reserveamerica.com, which sits behind an AWS WAF and answers plain HTTP
clients with a "Human Verification" page. But every state agency also keeps a
legacy server-rendered storefront on its own subdomain, and that one renders
the full per-site / per-night matrix straight into the HTML with no WAF and no
JavaScript. That is what we read.

The matrix markup is div-based, not a <table>:

    <div class='th calendar'><div class='date'>4</div><div class='weekday'>F</div>
    <div class='br'>
      <div class='td sn'> ...site number + siteId... </div>
      <div class='td loopName'>Sites 1-51</div>
      <div class='td status a'>A</div>     <- available that night
      <div class='td status r sat'>R</div> <- reserved
      ...14 nights total...
"""

import datetime as dt
import html
import http.cookiejar
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# state -> (legacy storefront host, contract code)
AGENCIES = {
    "CT": ("connecticutstateparks.reserveamerica.com", "CT"),
    "NY": ("newyorkstateparks.reserveamerica.com", "NY"),
    "MA": ("massdcrcamping.reserveamerica.com", "MA"),
    "RI": ("rhodeislandstateparks.reserveamerica.com", "RI"),
    # Vermont runs the same Aspira storefront on its own domain.
    "VT": ("vtstateparks-visit.com", "VT"),
}

STATUS = {
    "a": "available",
    "r": "reserved",
    "x": "closed",
    "w": "walk-in only",
    "n": "not available",
}


class Client:
    def __init__(self, host, contract):
        self.host = host
        self.contract = contract
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.opener.addheaders = [
            ("User-Agent", UA),
            ("Accept", "text/html,application/xhtml+xml"),
            ("Accept-Language", "en-US,en;q=0.9"),
        ]
        self._warm = False

    def _get(self, path, timeout=60):
        url = path if path.startswith("http") else f"https://{self.host}{path}"
        with self.opener.open(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")

    def warm(self):
        """The storefront needs a session cookie before it honours calarvdate."""
        if not self._warm:
            try:
                self._get("/welcome.do", timeout=45)
            except Exception:
                pass
            self._warm = True

    def campgrounds(self):
        """All bookable facilities for this agency, with park ids."""
        self._reset_session()
        out, seen, seen_ids = [], set(), set()
        pages = []
        # The directory lists 25 facilities per page; NY alone has ~180.
        for idx in range(0, 600, 25):
            try:
                p = self._get(
                    f"/campgroundDirectoryList.do?contractCode={self.contract}"
                    f"&startIdx={idx}"
                )
            except Exception:
                break
            ids = set(re.findall(r"parkId=(\d+)", p))
            if not ids or (pages and ids <= seen_ids):
                break
            seen_ids = ids
            pages.append(p)
        page = "\n".join(pages)
        # The directory renders each facility as a link whose text is the
        # facility name, plus sibling "Map"/"Enter Date" links on the same id.
        for m in re.finditer(r"parkId=(\d+)[^>]*>([^<]{3,80})<", page):
            pid = m.group(1)
            name = html.unescape(m.group(2)).strip()
            low = name.lower()
            if not name or low in ("map", "enter date") or low.startswith("view "):
                continue
            key = (pid, name)
            if key in seen:
                continue
            seen.add(key)
            if any(o["park_id"] == pid for o in out):
                continue
            out.append({"park_id": pid, "name": name, "state": self.contract})
        return out

    def _matrix_page(self, park_id, arrival, start_idx):
        q = urllib.parse.urlencode({
            "page": "matrix",
            "contractCode": self.contract,
            "parkId": park_id,
            "calarvdate": arrival.strftime("%m/%d/%Y"),
            "sitepage": "true",
            "startIdx": start_idx,
        })
        page = self._get(f"/campsiteCalendar.do?{q}")
        if "Human Verification" in page:
            raise RuntimeError("blocked by WAF")
        return page

    @staticmethod
    def _column_dates(page, arrival):
        """Grid headers carry day-of-month only; walk forward from arrival."""
        days = [int(d) for d in re.findall(
            r"<div class='date' id='day\d+date'>(\d+)</div>", page)]
        dates, cur = [], arrival
        for d in days:
            for _ in range(40):
                if cur.day == d:
                    break
                cur += dt.timedelta(days=1)
            dates.append(cur)
            cur += dt.timedelta(days=1)
        return dates

    @staticmethod
    def _parse_site_types(page, into):
        """Pull the Site type column from the list section of the page.

        Availability alone is misleading: a campground can show a dozen "open"
        rows that are all SHELTERS/PAVILIONS (day-use only) or an equestrian
        loop flagged HORSE REQUIRED TO CAMP. Type + loop is what makes an
        opening real, so we capture both and let the caller filter.
        """
        for row in re.split(r"<div class='br'>", page):
            if "siteListLabel" not in row or "maxPeopleCell" not in row:
                continue
            num = re.search(r"aria-label='Site:\s*([^'(]+?)\s*\(", row)
            if not num:
                continue
            label = html.unescape(num.group(1)).strip()
            cells = [
                html.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                for c in re.findall(r"<div class='td'[^>]*>(.*?)</div>\s*<div",
                                    row, re.S)
            ]
            tds = re.findall(r"<div class='td'>(.*?)</div>", row, re.S)
            vals = [re.sub(r"\s+", " ",
                           html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()
                    for t in tds]
            # The first cell is the "Map" link; loop and site type follow.
            vals = [v for v in vals if v and v.lower() != "map"]
            if len(vals) >= 2:
                into.setdefault(label, {})["loop"] = vals[0]
                into[label]["site_type"] = vals[1]

    @staticmethod
    def _parse_rows(page, dates, into):
        for row in re.split(r"<div class='br'>", page):
            if "td sn" not in row or "td status" not in row:
                continue
            sid = re.search(r"siteId=(\d+)", row)
            num = re.search(r"aria-label='Site:\s*([^'(]+?)\s*\(", row)
            loop = re.search(r"class='td loopName'[^>]*>([^<]*)<", row)
            statuses = re.findall(r"class='td status ([a-z])[^']*'", row)
            if not (sid and statuses):
                continue
            label = html.unescape(num.group(1)).strip() if num else sid.group(1)
            into[label] = {
                "site_id": sid.group(1),
                "loop": html.unescape(loop.group(1)).strip() if loop else "",
                "nights": {
                    dates[i].isoformat(): STATUS.get(s, s)
                    for i, s in enumerate(statuses[:len(dates)])
                },
            }

    def _reset_session(self):
        """Drop server-side search state.

        The storefront remembers the previous query's site-type filter in the
        session; reusing a session across parks/dates silently returns a
        filtered subset (e.g. 14 rows instead of 89) with no error.
        """
        self.jar.clear()
        self._warm = False
        self.warm()

    def matrix(self, park_id, arrival, max_pages=12):
        """Per-site, per-night availability for the 2 weeks from `arrival`.

        The storefront pages the site list 25 rows at a time, so a big
        campground needs several fetches - reading only page 1 silently hides
        most of the sites (and most of the openings).
        """
        self._reset_session()
        sites, types, dates, total = {}, {}, [], None
        for p in range(max_pages):
            page = self._matrix_page(park_id, arrival, p * 25)
            if p == 0:
                dates = self._column_dates(page, arrival)
                # Early-out: the header reports availability across the whole
                # 2-week window. Zero there means zero for any range inside it,
                # so skip paging the rest of a sold-out campground.
                head = re.sub(r"<[^>]+>", " ", page)
                z = re.search(r"(\d+)\s*site\(s\) available out of\s*(\d+)", head)
                if z:
                    total_sites_hint = int(z.group(2))
                    if int(z.group(1)) == 0:
                        return {"dates": [d.isoformat() for d in dates],
                                "sites": {}, "reported_total": total_sites_hint,
                                "sold_out": True}
                flat = re.sub(r"<[^>]+>", " ", page)
                flat = re.sub(r"\s+", " ", flat)
                m = re.search(r"Campsite Search Results:\s*\d+\s*-\s*\d+\s*of\s*(\d+)",
                              flat)
                if not m:
                    m = re.search(r"out of\s*(\d+)\s*site", flat)
                total = int(m.group(1)) if m else None
            before = len(sites)
            self._parse_rows(page, dates, sites)
            self._parse_site_types(page, types)
            if len(sites) == before:
                break            # page produced nothing new: end of list
            if total and len(sites) >= total:
                break            # we have every site the storefront reports
            if not total and len(sites) - before < 25:
                break            # no total to trust; a short page means the end
        for label, rec in sites.items():
            meta = types.get(label, {})
            rec["site_type"] = meta.get("site_type", "")
            if meta.get("loop"):
                rec["loop"] = meta["loop"]
        return {"dates": [d.isoformat() for d in dates], "sites": sites,
                "reported_total": total}


    # Field names as they appear on the campsiteDetails page.
    _DETAIL_KEYS = {
        "Pets Allowed": "pets_allowed",
        "Maximum Number of Pets": "max_pets",
        "Waterfront Site": "waterfront",
        "Site Access": "access",
        "Max Num of People": "max_people",
        "Capacity/Size Rating": "capacity",
        "Campfire Allowed": "campfire",
        "Shade": "shade",
        "GIS Details Latitude": "lat",
        "GIS Details Longitude": "lng",
        "Driveway Grade": "driveway_grade",
        "Max Vehicle Length": "max_vehicle_len",
    }

    def site_details(self, park_id, site_id, arrival):
        """Per-site facts straight from the booking record.

        This is the authoritative answer to the two questions that actually
        decide the trip: `Waterfront Site: Y/N` and `Pets Allowed`. Marketing
        copy and third-party listings get both wrong constantly; this is what
        the reservation system itself holds.
        """
        self._reset_session()
        q = urllib.parse.urlencode({
            "contractCode": self.contract,
            "parkId": park_id,
            "siteId": site_id,
            "arvdate": arrival.strftime("%m/%d/%Y"),
        })
        page = self._get(f"/campsiteDetails.do?{q}")
        text = re.sub(r"<script.*?</script>", " ", page, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(re.sub(r"\s+", " ", text))
        out = {}
        for label, key in self._DETAIL_KEYS.items():
            m = re.search(
                re.escape(label) + r":\s*([^:]{1,40}?)\s+(?=[A-Z][A-Za-z/ ]{2,30}:|$)",
                text,
            )
            if m:
                out[key] = m.group(1).strip()
        return out

    def open_for(self, park_id, checkin, checkout):
        """Sites available every night of [checkin, checkout)."""
        nights = []
        d = checkin
        while d < checkout:
            nights.append(d.isoformat())
            d += dt.timedelta(days=1)
        grid = self.matrix(park_id, checkin)
        out = {}
        for label, rec in grid["sites"].items():
            got = [rec["nights"].get(n) for n in nights]
            if got and all(g == "available" for g in got):
                out[label] = {
                    "site_id": rec["site_id"],
                    "loop": rec["loop"],
                    "nights": nights,
                    "book_url": (
                        f"https://{self.host}/campsiteDetails.do"
                        f"?contractCode={self.contract}&parkId={park_id}"
                        f"&siteId={rec['site_id']}"
                        f"&arvdate={checkin.strftime('%m/%d/%Y')}"
                    ),
                }
        return {"total_sites": len(grid["sites"]), "open": out,
                "partial": {
                    label: rec["nights"]
                    for label, rec in grid["sites"].items()
                    if any(rec["nights"].get(n) == "available" for n in nights)
                    and label not in out
                }}


def client(state):
    host, contract = AGENCIES[state]
    return Client(host, contract)
