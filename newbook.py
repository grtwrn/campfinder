"""Newbook online-booking availability (Odetah Camping Resort and others).

Newbook's consumer page loads its category grid with one XHR:

    POST {api_path}newbook_api_action=availability_chart_responsive
         available_from=4 Sep 2026&available_to=6 Sep 2026&nights=2&adults=2...

`api_path` is a per-instance `generated_api_files/<hash>.php?` URL printed
into the booking page as `newbook_api_path=...`, so we read it from the page
first. The response is an HTML fragment: one `newbook_online_category_box`
per accommodation category. A category is bookable when its action row holds
priced tariff buttons; a sold-out one carries the "It appears the
accommodation options and dates you have selected..." message instead.
"""

import datetime as dt
import html
import http.cookiejar
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

SOLD_OUT_TEXT = "It appears the accommodation options and dates you have sele"


class Client:
    def __init__(self, booking_url):
        """booking_url: e.g. https://bookingsus.newbook.cloud/online/odetah_camping_resort"""
        self.booking_url = booking_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", UA)]
        page = self.opener.open(booking_url, timeout=60).read().decode(
            "utf-8", "replace")
        m = re.search(r"newbook_api_path='([^']+)'", page)
        if not m:
            raise RuntimeError("could not find newbook_api_path on booking page")
        self.api_path = m.group(1)

    def availability(self, checkin, checkout, adults=2):
        nights = (checkout - checkin).days
        data = urllib.parse.urlencode({
            "available_from": checkin.strftime("%-d %b %Y"),
            "available_to": checkout.strftime("%-d %b %Y"),
            "nights": nights, "adults": adults, "children": 0, "infants": 0,
            "equipment_type": "", "equipment_length": "",
            "equipment_measurement_unit": "", "language": "",
            "existing_guest_equipment": "",
        }).encode()
        req = urllib.request.Request(
            self.api_path + "newbook_api_action=availability_chart_responsive",
            data=data, headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.booking_url,
            })
        frag = self.opener.open(req, timeout=90).read().decode("utf-8", "replace")
        boxes = re.findall(
            r"(<div[^>]*class=\"[^\"]*newbook_online_category_box[^\"]*\"[^>]*>.*?)"
            r"(?=<div[^>]*class=\"[^\"]*newbook_online_category_box|\Z)",
            frag, re.S)
        out = []
        for b in boxes:
            name = re.search(r"<h3>.*?>([^<]+)</a>", b, re.S)
            name = html.unescape(name.group(1)).strip() if name else "?"
            text = html.unescape(re.sub(r"<[^>]+>", " ", b))
            text = re.sub(r"\s+", " ", text)
            prices = re.findall(r"\$\s?(\d[\d,]*(?:\.\d\d)?)", text)
            sold_out = SOLD_OUT_TEXT in text
            min_stay = re.findall(r"(\d+) Night min", text)
            out.append({
                "category": name,
                "available": (not sold_out) and bool(prices),
                "from_price": float(prices[0].replace(",", "")) if prices else None,
                "min_nights": min(int(x) for x in min_stay) if min_stay else None,
                "note": ("sold out for these dates" if sold_out else
                         (f"{min_stay[0]}-night minimum" if min_stay else "")),
            })
        return out
