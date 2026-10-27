"""多城市行程：

1. 多段航线（顺序固定）：每段分别查单程票，给出每段的候选和合计。
2. 自动排顺序：给出要去的几个城市和每个城市停留天数，枚举所有顺序，找总价最低的走法。
   有 Travelpayouts 时用免费缓存价估算，不消耗付费额度；否则用实时查询，并限制调用次数。
"""

from __future__ import annotations

import asyncio
import itertools
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field

from .models import SearchQuery
from .places import display_name, same_place
from .providers import ProviderError
from .scoring import Preferences
from .service import Planner

MAX_LEGS = 6
MAX_CITIES = 6
DEFAULT_MAX_LIVE_CALLS = 20


class Leg(BaseModel):
    origin: str
    destination: str
    date: date
    flight_number: str | None = None  # 指定这一段的航班，如 JU 652


class Stop(BaseModel):
    place: str
    days: int = Field(ge=1, le=60)


# ---------- 多段航线 ----------
async def multi_leg(planner: Planner, legs: list[Leg], prefs: Preferences, per_leg: int = 6, **query_kwargs) -> dict:
    if not 2 <= len(legs) <= MAX_LEGS:
        raise ProviderError(f"多段航线需要 2 到 {MAX_LEGS} 段")
    for a, b in zip(legs, legs[1:]):
        if b.date < a.date:
            raise ProviderError(f"第 {legs.index(b) + 1} 段的日期早于上一段")
    queries = [
        SearchQuery(origin=l.origin, destination=l.destination, depart_date=l.date, flight_number=l.flight_number or None, **query_kwargs)
        for l in legs
    ]
    results = await asyncio.gather(*(planner.search(q, prefs) for q in queries))
    out_legs = []
    totals = {"cheapest": 0.0, "best": 0.0}
    complete = True
    warnings: list[str] = []
    for i, (leg, res) in enumerate(zip(legs, results)):
        its = res.itineraries
        warnings += [f"第 {i + 1} 段：{e}" for e in res.errors]
        if not its:
            complete = False
        else:
            totals["cheapest"] += min(it.comparable_price() for it in its)
            totals["best"] += next((it for it in its if "best" in it.tags), its[0]).comparable_price()
        out_legs.append({**res.to_view(), "itineraries": [it.to_view() for it in its[:per_leg]], "total_found": len(its)})
    # 相邻两段：前一段最早到达是否晚于后一段最早出发（同一天转机时提醒）
    for i in range(len(legs) - 1):
        if legs[i].date == legs[i + 1].date:
            warnings.append(f"第 {i + 1}、{i + 2} 段在同一天，分开出票请留足转机时间，误机风险自负")
    return {
        "legs": out_legs,
        "total_cheapest": round(totals["cheapest"], 2) if complete else None,
        "total_best": round(totals["best"], 2) if complete else None,
        "currency": planner.settings.currency,
        "warnings": warnings,
        "note": "每段单独出票。航司的联程票有时更便宜，可以用 Google Flights 的多城市搜索对比。",
    }


# ---------- 自动排顺序 ----------
@dataclass
class _Plan:
    order: tuple[str, ...]
    legs: list[tuple[str, str, date]]


def _plans(home: str, stops: list[Stop], start: date, return_home: bool, start_flex_days: int) -> list[_Plan]:
    plans = []
    for offset in range(-start_flex_days, start_flex_days + 1):
        day0 = start + timedelta(days=offset)
        if day0 < date.today():
            continue
        for perm in itertools.permutations(stops):
            legs = []
            here, d = home, day0
            for s in perm:
                legs.append((here, s.place, d))
                here, d = s.place, d + timedelta(days=s.days)
            if return_home:
                legs.append((here, home, d))
            plans.append(_Plan(tuple(s.place for s in perm), legs))
    return plans


class _CalendarPrices:
    """用缓存价日历查每段价格；某天没数据时用这条航线附近几天的中位数估算。"""

    def __init__(self, planner: Planner, start: date, end: date):
        self.planner, self.start, self.end = planner, start, end
        self.by_pair: dict[tuple[str, str], dict[date, float]] = {}
        self.errors: list[str] = []

    async def load(self, pairs: set[tuple[str, str]]) -> None:
        async def one(pair):
            try:
                res = await self.planner.flexible(pair[0], pair[1], self.start, self.end, None)
                self.by_pair[pair] = {date.fromisoformat(q["depart_date"]): q["price"] for q in res["quotes"]}
            except ProviderError as e:
                self.errors.append(f"{display_name(pair[0])}→{display_name(pair[1])}：{e}")
                self.by_pair[pair] = {}

        sem = asyncio.Semaphore(4)

        async def guarded(pair):
            async with sem:
                await one(pair)

        await asyncio.gather(*(guarded(p) for p in pairs))

    def price(self, o: str, d: str, day: date) -> tuple[float | None, bool]:
        prices = self.by_pair.get((o, d), {})
        if day in prices:
            return prices[day], False
        near = [p for dd, p in prices.items() if abs((dd - day).days) <= 3]
        if near:
            return statistics.median(near), True
        return None, False


async def optimize_order(
    planner: Planner,
    home: str,
    stops: list[Stop],
    start: date,
    return_home: bool = True,
    start_flex_days: int = 0,
    live: bool = False,
    max_live_calls: int = DEFAULT_MAX_LIVE_CALLS,
    top: int = 10,
) -> dict:
    if not 2 <= len(stops) <= MAX_CITIES:
        raise ProviderError(f"请给 2 到 {MAX_CITIES} 个城市")
    if len({s.place for s in stops}) != len(stops):
        raise ProviderError("城市有重复")
    if any(same_place(home, s.place) for s in stops):
        raise ProviderError("要去的城市里不要包含出发地")
    start_flex_days = max(0, min(start_flex_days, 3))
    plans = _plans(home, stops, start, return_home, start_flex_days)
    if not plans:
        raise ProviderError("出发日期已过")
    needed = {(o, d, day) for p in plans for (o, d, day) in p.legs}
    pairs = {(o, d) for (o, d, _) in needed}
    days = [day for (_, _, day) in needed]
    errors: list[str] = []

    use_live = live or planner.calendar is None
    if use_live:
        if not planner.flight_providers:
            raise ProviderError("没有可用的数据源")
        if len(needed) > max_live_calls:
            raise ProviderError(
                f"实时查询需要 {len(needed)} 次搜索，超过上限 {max_live_calls} 次。"
                "可以减少城市、把出发日浮动设为 0，或在 .env 填写免费的 TRAVELPAYOUTS_TOKEN 用缓存价估算"
            )
        live_prices: dict[tuple[str, str, date], float | None] = {}

        async def fetch(key):
            o, d, day = key
            res = await planner.search(SearchQuery(origin=o, destination=d, depart_date=day), Preferences())
            live_prices[key] = min((it.comparable_price() for it in res.itineraries), default=None)
            errors.extend(e for e in res.errors if e not in errors)

        sem = asyncio.Semaphore(3)

        async def guarded(key):
            async with sem:
                await fetch(key)

        await asyncio.gather(*(guarded(k) for k in needed))

        def price(o, d, day):
            return live_prices.get((o, d, day)), False
    else:
        cal = _CalendarPrices(planner, min(days), max(days))
        await cal.load(pairs)
        errors += cal.errors
        price = cal.price

    ranked = []
    for p in plans:
        legs = []
        total = 0.0
        ok = True
        estimated = False
        for o, d, day in p.legs:
            pr, est = price(o, d, day)
            estimated = estimated or est
            if pr is None:
                ok = False
            else:
                total += pr
            legs.append({"origin": o, "destination": d, "origin_name": display_name(o), "destination_name": display_name(d), "date": day.isoformat(), "price": pr, "estimated": est})
        ranked.append({
            "order": [display_name(c) for c in p.order],
            "order_codes": list(p.order),
            "start": p.legs[0][2].isoformat(),
            "legs": legs,
            "total": round(total, 2) if ok else None,
            "estimated": estimated,
            "complete": ok,
        })
    ranked.sort(key=lambda r: (not r["complete"], r["total"] if r["total"] is not None else 0))
    complete = [r for r in ranked if r["complete"]]
    return {
        "home": home,
        "home_name": display_name(home),
        "currency": planner.settings.currency,
        "price_type": "实时价" if use_live else "缓存价估算",
        "searches_used": len(needed) if use_live else 0,
        "plans_checked": len(plans),
        "results": ranked[:top],
        "worst_total": max((r["total"] for r in complete), default=None),
        "errors": errors,
    }
