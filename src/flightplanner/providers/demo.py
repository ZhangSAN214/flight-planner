"""演示数据：没有填写任何 API key 时使用，让界面和排序逻辑可以先跑起来。

数据是按航线和日期确定性生成的假数据，同样的查询每次结果相同。
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta

from ..models import BookingOption, DateQuote, Itinerary, SearchQuery, Segment, Slice
from .base import FlightProvider, PriceCalendarProvider

AIRLINES = [
    ("BA", "British Airways"),
    ("EK", "Emirates"),
    ("QR", "Qatar Airways"),
    ("TK", "Turkish Airlines"),
    ("KL", "KLM"),
    ("LH", "Lufthansa"),
    ("AY", "Finnair"),
    ("MU", "China Eastern"),
    ("CA", "Air China"),
    ("FR", "Ryanair"),
]
HUBS = ["DXB", "DOH", "IST", "AMS", "FRA", "HEL", "CDG", "PEK"]
ANYWHERE = ["BCN", "DUB", "AMS", "CDG", "FCO", "LIS", "PRG", "BUD", "KRK", "IST", "DXB", "PVG", "PEK", "HKG", "NRT", "BKK", "SIN", "JFK"]


def _rng(*parts: object) -> random.Random:
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


# 常见机场坐标，用来估算演示数据的飞行时间；不在表里的按代码随机生成
COORDS = {
    "MAN": (53.35, -2.27), "LHR": (51.47, -0.45), "LGW": (51.15, -0.19), "LPL": (53.33, -2.85), "EDI": (55.95, -3.36),
    "DUB": (53.42, -6.27), "AMS": (52.31, 4.76), "CDG": (49.01, 2.55), "FRA": (50.04, 8.56), "BCN": (41.30, 2.08),
    "FCO": (41.80, 12.25), "LIS": (38.77, -9.13), "PRG": (50.10, 14.26), "BUD": (47.44, 19.26), "KRK": (50.08, 19.78),
    "HEL": (60.32, 24.96), "IST": (41.26, 28.74), "DXB": (25.25, 55.36), "DOH": (25.27, 51.61), "PVG": (31.14, 121.81),
    "SHA": (31.20, 121.34), "PEK": (40.08, 116.58), "PKX": (39.51, 116.41), "CAN": (23.39, 113.30), "SZX": (22.64, 113.81),
    "CTU": (30.58, 103.95), "HKG": (22.31, 113.91), "NRT": (35.77, 140.39), "ICN": (37.46, 126.44), "BKK": (13.69, 100.75),
    "SIN": (1.36, 103.99), "JFK": (40.64, -73.78), "LAX": (33.94, -118.41), "SYD": (-33.94, 151.18),
}


def _route_base(origin: str, destination: str) -> tuple[int, float]:
    """返回（直飞分钟数，基础票价），按航线固定。"""
    r = _rng("route", *sorted([origin, destination]))
    if origin in COORDS and destination in COORDS:
        (la1, lo1), (la2, lo2) = COORDS[origin], COORDS[destination]
        p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
        km = 6371 * math.acos(min(1.0, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)))
        minutes = int(km / 820 * 60 + 35)
    else:
        minutes = r.randint(90, 780)
    return minutes, 35 + minutes * r.uniform(0.45, 0.7)


def _slice(r: random.Random, origin: str, destination: str, day: date, base_minutes: int, stops: int, airline: tuple[str, str]) -> Slice:
    code, name = airline
    hour = r.choice([0, 1, 6, 7, 8, 9, 10, 11, 13, 14, 15, 17, 19, 21, 22, 23])
    t = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=r.choice([0, 15, 30, 45]))
    points = [origin] + r.sample(HUBS, stops) + [destination]
    segments: list[Segment] = []
    leg_total = base_minutes * (1 + 0.18 * stops)
    for i in range(len(points) - 1):
        leg = int(leg_total / (len(points) - 1) + r.randint(-20, 20))
        seg = Segment(
            carrier_code=code,
            carrier_name=name,
            flight_number=f"{code} {r.randint(10, 999)}",
            origin=points[i],
            destination=points[i + 1],
            depart=t,
            arrive=t + timedelta(minutes=leg),
            duration_minutes=leg,
        )
        segments.append(seg)
        t = seg.arrive + timedelta(minutes=r.choice([55, 80, 110, 150, 210, 300, 480, 720]))
    total = int((segments[-1].arrive - segments[0].depart).total_seconds() // 60)
    return Slice(segments=segments, duration_minutes=total)


class DemoFlightProvider(FlightProvider):
    name = "演示数据"

    def __init__(self, currency: str = "GBP"):
        self.currency = currency

    async def search(self, query: SearchQuery) -> list[Itinerary]:
        base_minutes, base_price = _route_base(query.origin, query.destination)
        r = _rng("search", query.origin, query.destination, query.depart_date, query.return_date)
        demand = 1 + 0.25 * math.sin(query.depart_date.toordinal() / 5)
        result = []
        for i in range(24):
            airline = r.choice(AIRLINES if base_minutes < 300 else [a for a in AIRLINES if a[0] != "FR"])
            stops = 0 if (base_minutes < 240 and r.random() < 0.7) else r.choice([0, 1, 1, 1, 2])
            if query.max_stops is not None:
                stops = min(stops, query.max_stops)
            slices = [_slice(r, query.origin, query.destination, query.depart_date, base_minutes, stops, airline)]
            if query.return_date:
                slices.append(_slice(r, query.destination, query.origin, query.return_date, base_minutes, stops, airline))
            price = base_price * demand * (1.35 if stops == 0 else 1.0 - 0.06 * stops) * r.uniform(0.75, 1.35)
            price *= 1.8 if query.return_date else 1
            price *= {"economy": 1, "premium_economy": 1.7, "business": 3.8, "first": 6}[query.cabin.value]
            result.append(
                Itinerary(
                    id=f"demo-{query.origin}{query.destination}-{query.depart_date:%m%d}-{i}",
                    source=self.name,
                    price=round(price * query.adults),
                    currency=self.currency,
                    slices=slices,
                    booking_token=f"demo-{i}",
                )
            )
        return result

    async def booking_options(self, itinerary: Itinerary, query: SearchQuery) -> list[BookingOption]:
        r = _rng("booking", itinerary.id)
        p = itinerary.price
        airline = itinerary.airlines[0] if itinerary.airlines else "航司"
        options = [
            BookingOption(seller=f"{airline}（官网）", is_airline=True, price=p, currency=self.currency, fare_name="Economy Classic", baggage=["含 1 件 23kg 托运行李"]),
            BookingOption(seller="Trip.com", price=round(p * r.uniform(0.93, 1.03)), currency=self.currency, fare_name="经济舱", baggage=["含 1 件托运行李"]),
            BookingOption(seller="Expedia", price=round(p * r.uniform(0.98, 1.06)), currency=self.currency, baggage=["行李以航司为准"]),
            BookingOption(seller="eDreams", price=round(p * r.uniform(0.86, 0.95)), currency=self.currency, fare_name="Basic", baggage=["不含托运行李，加购约 £45"], conditions=["不可退改"]),
        ]
        for o in options:
            o.source = self.name
        return options


class DemoCalendarProvider(PriceCalendarProvider):
    name = "演示数据（缓存价）"

    def __init__(self, currency: str = "GBP"):
        self.currency = currency

    def _price(self, origin: str, destination: str, day: date, trip_days: int | None) -> float:
        _, base = _route_base(origin, destination)
        r = _rng("cal", origin, destination, day, trip_days)
        weekday = {1: 0.88, 2: 0.85, 5: 1.15, 6: 1.1}.get(day.weekday(), 1.0)
        wave = 1 + 0.2 * math.sin(day.toordinal() / 6)
        sale = 0.6 if r.random() < 0.06 else 1.0  # 偶尔出现特价日
        return round(base * weekday * wave * sale * r.uniform(0.9, 1.1) * (1.8 if trip_days else 1))

    async def date_prices(self, origin: str, destination: str, start: date, end: date, trip_days: int | None) -> list[DateQuote]:
        quotes = []
        d = start
        while d <= end:
            quotes.append(
                DateQuote(
                    depart_date=d,
                    return_date=d + timedelta(days=trip_days) if trip_days else None,
                    price=self._price(origin, destination, d, trip_days),
                    currency=self.currency,
                    source=self.name,
                    destination=destination,
                )
            )
            d += timedelta(days=1)
        return quotes

    async def anywhere(self, origin: str, month: str, trip_days: int | None) -> list[DateQuote]:
        year, mon = map(int, month.split("-"))
        quotes = []
        for dest in ANYWHERE:
            if dest == origin:
                continue
            r = _rng("anywhere", origin, dest, month)
            day = date(year, mon, r.randint(1, 28))
            quotes.append(
                DateQuote(
                    depart_date=day,
                    return_date=day + timedelta(days=trip_days) if trip_days else None,
                    price=self._price(origin, dest, day, trip_days),
                    currency=self.currency,
                    source=self.name,
                    destination=dest,
                    airline=r.choice(AIRLINES)[0],
                    transfers=r.choice([0, 0, 1]),
                )
            )
        quotes.sort(key=lambda q: q.price)
        return quotes
