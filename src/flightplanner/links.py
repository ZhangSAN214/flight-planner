"""生成各比价网站的搜索链接（已填好航线和日期），用于没有接口的平台和最终核价。"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from .models import Cabin, SearchQuery

_SKY_CABIN = {Cabin.economy: "economy", Cabin.premium_economy: "premiumeconomy", Cabin.business: "business", Cabin.first: "first"}
_TRIP_CABIN = {Cabin.economy: "y", Cabin.premium_economy: "s", Cabin.business: "c", Cabin.first: "f"}
_KAYAK_CABIN = {Cabin.economy: "", Cabin.premium_economy: "/premium", Cabin.business: "/business", Cabin.first: "/first"}


def search_links(q: SearchQuery) -> list[dict]:
    o, d = q.origin, q.destination
    dep, ret = q.depart_date, q.return_date
    links: list[dict] = []

    text = f"Flights from {o} to {d} on {dep.isoformat()}" + (f" returning {ret.isoformat()}" if ret else " one way")
    links.append({"name": "Google Flights", "url": "https://www.google.com/travel/flights?" + urlencode({"q": text, "curr": "GBP"})})

    sky = f"https://www.skyscanner.net/transport/flights/{o.lower()}/{d.lower()}/{dep:%y%m%d}/"
    if ret:
        sky += f"{ret:%y%m%d}/"
    sky += "?" + urlencode({"adultsv2": q.adults, "cabinclass": _SKY_CABIN[q.cabin]})
    links.append({"name": "Skyscanner", "url": sky})

    trip = {
        "dcity": o.lower(),
        "acity": d.lower(),
        "ddate": dep.isoformat(),
        "triptype": "rt" if ret else "ow",
        "class": _TRIP_CABIN[q.cabin],
        "quantity": q.adults,
    }
    if ret:
        trip["rdate"] = ret.isoformat()
    links.append({"name": "Trip.com（携程国际版）", "url": "https://uk.trip.com/flights/showfarefirst?" + urlencode(trip)})

    if ret:
        ctrip = f"https://flights.ctrip.com/online/list/round-{o.lower()}-{d.lower()}?" + urlencode({"depdate": f"{dep.isoformat()}_{ret.isoformat()}", "adult": q.adults})
    else:
        ctrip = f"https://flights.ctrip.com/online/list/oneway-{o.lower()}-{d.lower()}?" + urlencode({"depdate": dep.isoformat(), "adult": q.adults})
    links.append({"name": "携程（国内版）", "url": ctrip})

    kayak = f"https://www.kayak.co.uk/flights/{o}-{d}/{dep.isoformat()}" + (f"/{ret.isoformat()}" if ret else "")
    kayak += _KAYAK_CABIN[q.cabin] + (f"/{q.adults}adults" if q.adults > 1 else "") + "?sort=price_a"
    links.append({"name": "Kayak", "url": kayak})

    links.append({"name": "去哪儿（搜索）", "url": "https://www.qunar.com/?" + urlencode({"keyword": f"{o}-{d} {dep.isoformat()}"})})
    links.append({"name": "飞猪（搜索）", "url": "https://s.fliggy.com/?q=" + quote(f"{o} {d} 机票")})
    return links
