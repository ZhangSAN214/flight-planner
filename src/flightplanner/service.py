"""把各数据源组合起来：搜索、去重、换算货币、排序、比价、灵活日期、任意目的地。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from .config import Settings
from .db import Store
from .fx import FxConverter
from .links import search_links
from .models import BookingOption, DateQuote, Itinerary, SearchQuery
from .places import display_name
from .providers import FlightProvider, PriceCalendarProvider, ProviderError
from .providers.demo import DemoCalendarProvider, DemoFlightProvider
from .providers.duffel import DuffelProvider
from .providers.serpapi import SerpApiProvider
from .providers.travelpayouts import TravelpayoutsProvider
from .scoring import Preferences, dedupe, rank

log = logging.getLogger(__name__)

ITIN_CACHE_MINUTES = 180
# 没有缓存价数据源时，灵活日期最多逐天实时查询的天数（控制 API 用量）
MAX_LIVE_FLEX_DAYS = 7


@dataclass
class SearchResult:
    query: SearchQuery
    itineraries: list[Itinerary]
    errors: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    from_cache: bool = False

    def airline_summary(self) -> list[dict]:
        """这条航线上每家航司（组合）的最低价、班次数、是否有直飞。"""
        groups: dict[str, dict] = {}
        for it in self.itineraries:
            name = " + ".join(it.airlines) or "未知"
            g = groups.setdefault(name, {"airline": name, "codes": sorted(it.carrier_codes), "min_price": None, "count": 0, "nonstop": False})
            price = it.comparable_price()
            g["min_price"] = price if g["min_price"] is None else min(g["min_price"], price)
            g["count"] += 1
            g["nonstop"] = g["nonstop"] or it.total_stops == 0
        return sorted(groups.values(), key=lambda g: g["min_price"])

    def to_view(self) -> dict:
        return {
            "query": self.query.model_dump(mode="json"),
            "origin_name": display_name(self.query.origin),
            "destination_name": display_name(self.query.destination),
            "airline_summary": self.airline_summary(),
            "itineraries": [i.to_view() for i in self.itineraries],
            "errors": self.errors,
            "sources": self.sources,
            "from_cache": self.from_cache,
            "links": search_links(self.query),
        }


class Planner:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        flight_providers: list[FlightProvider] | None = None,
        calendar: PriceCalendarProvider | None = None,
        fx: FxConverter | None = None,
    ):
        self.settings = settings
        self.store = store
        client = httpx.AsyncClient(timeout=60)
        if flight_providers is None:
            flight_providers = []
            if settings.demo_mode:
                flight_providers.append(DemoFlightProvider(settings.currency))
            else:
                if settings.serpapi_key:
                    flight_providers.append(SerpApiProvider(settings.serpapi_key, store, settings.currency, settings.serpapi_monthly_limit, client))
                if settings.duffel_token:
                    flight_providers.append(DuffelProvider(settings.duffel_token, client))
        if calendar is None:
            if settings.demo_mode:
                calendar = DemoCalendarProvider(settings.currency)
            elif settings.travelpayouts_token:
                calendar = TravelpayoutsProvider(settings.travelpayouts_token, settings.currency, client)
        self.flight_providers = flight_providers
        self.calendar = calendar
        self.fx = fx or FxConverter(store, settings.currency, client)

    # ---- 搜索 ----
    async def _search_one(self, provider: FlightProvider, query: SearchQuery) -> list[Itinerary]:
        key = f"search:{provider.name}:{query.cache_key()}"
        cached = self.store.cache_get(key)
        if cached is not None:
            return [Itinerary.model_validate(i) for i in cached]
        itins = await provider.search(query)
        self.store.cache_set(key, [i.model_dump(mode="json") for i in itins], self.settings.cache_minutes)
        return itins

    async def search(self, query: SearchQuery, prefs: Preferences | None = None) -> SearchResult:
        prefs = prefs or Preferences()
        if not self.flight_providers:
            return SearchResult(query, [], errors=["还没有可用的实时数据源：请在 .env 填写 SERPAPI_KEY 或 DUFFEL_TOKEN"])
        results = await asyncio.gather(*(self._search_one(p, query) for p in self.flight_providers), return_exceptions=True)
        itins: list[Itinerary] = []
        errors: list[str] = []
        sources: list[str] = []
        for provider, res in zip(self.flight_providers, results):
            if isinstance(res, ProviderError):
                errors.append(str(res))
            elif isinstance(res, Exception):
                log.exception("数据源 %s 出错", provider.name, exc_info=res)
                errors.append(f"{provider.name} 出错：{res}")
            else:
                sources.append(provider.name)
                itins += res
        if query.airlines or query.flight_number:
            before = len(itins)
            itins = [it for it in itins if it.matches(query.airlines, query.flight_number)]
            if before and not itins:
                what = query.flight_number or "、".join(query.airlines)
                errors.append(f"找到 {before} 个方案，但没有符合「{what}」的。可能这天没有这个航班，或航司不飞这条航线")
        for it in itins:
            it.price_display = await self.fx.convert(it.price, it.currency)
        if any(it.price_display is None for it in itins):
            errors.append("部分报价无法换算货币，已按原币种参与比较")
        itins = rank(dedupe(itins), prefs)
        for it in itins:
            self.store.cache_set(f"itin:{it.id}", {"itinerary": it.model_dump(mode="json"), "query": query.model_dump(mode="json")}, ITIN_CACHE_MINUTES)
        return SearchResult(query, itins, errors, sources)

    # ---- 多渠道比价 ----
    async def booking_options(self, itinerary_id: str) -> dict:
        saved = self.store.cache_get(f"itin:{itinerary_id}")
        if not saved:
            raise ProviderError("这个方案已过期，请重新搜索")
        itin = Itinerary.model_validate(saved["itinerary"])
        query = SearchQuery.model_validate(saved["query"])
        cache_key = f"booking:{itinerary_id}"
        cached = self.store.cache_get(cache_key)
        if cached is not None:
            options = [BookingOption.model_validate(o) for o in cached]
        else:
            provider = next((p for p in self.flight_providers if p.name == itin.source), None)
            options = await provider.booking_options(itin, query) if provider else []
            self.store.cache_set(cache_key, [o.model_dump(mode="json") for o in options], self.settings.cache_minutes)
        rows = []
        for o in options:
            display = await self.fx.convert(o.price, o.currency) if o.price is not None else None
            rows.append({**o.model_dump(mode="json"), "price_display": display})
        rows.sort(key=lambda r: (r["price_display"] is None, r["price_display"] or 0))
        if rows and rows[0]["price_display"] is not None:
            rows[0]["cheapest"] = True
        return {"itinerary": itin.to_view(), "options": rows, "links": search_links(query)}

    # ---- 灵活日期 ----
    async def flexible(self, origin: str, destination: str, start: date, end: date, trip_days: int | None) -> dict:
        origin, destination = origin.upper(), destination.upper()
        if end < start:
            raise ProviderError("结束日期早于开始日期")
        if (end - start).days > 92:
            raise ProviderError("日期范围最多 3 个月")
        errors: list[str] = []
        quotes: list[DateQuote] = []
        live = False
        if self.calendar:
            key = f"cal:{self.calendar.name}:{origin}:{destination}:{start}:{end}:{trip_days}"
            cached = self.store.cache_get(key)
            if cached is not None:
                quotes = [DateQuote.model_validate(q) for q in cached]
            else:
                quotes = await self.calendar.date_prices(origin, destination, start, end, trip_days)
                self.store.cache_set(key, [q.model_dump(mode="json") for q in quotes], max(self.settings.cache_minutes, 120))
        elif self.flight_providers:
            # 没有缓存价数据源：逐天实时查询，限制天数控制费用
            live = True
            days = min((end - start).days + 1, MAX_LIVE_FLEX_DAYS)
            if (end - start).days + 1 > days:
                errors.append(f"未配置 Travelpayouts，只实时查询了前 {days} 天")
            for i in range(days):
                d = start + timedelta(days=i)
                q = SearchQuery(origin=origin, destination=destination, depart_date=d, return_date=d + timedelta(days=trip_days) if trip_days else None)
                res = await self.search(q)
                errors += [e for e in res.errors if e not in errors]
                if res.itineraries:
                    cheapest = min(res.itineraries, key=lambda it: it.comparable_price())
                    quotes.append(DateQuote(depart_date=d, return_date=q.return_date, price=cheapest.comparable_price(), currency=self.settings.currency, source=cheapest.source))
        else:
            errors.append("没有可用数据源")
        for q in quotes:
            if q.currency.upper() != self.settings.currency:
                converted = await self.fx.convert(q.price, q.currency)
                if converted is not None:
                    q.price, q.currency = converted, self.settings.currency
        prices = sorted(q.price for q in quotes)
        median = prices[len(prices) // 2] if prices else None
        cheapest_days = sorted(quotes, key=lambda q: q.price)[:5]
        return {
            "origin": origin,
            "destination": destination,
            "trip_days": trip_days,
            "currency": self.settings.currency,
            "live": live,
            "median": median,
            "quotes": [q.model_dump(mode="json") for q in quotes],
            "cheapest": [q.model_dump(mode="json") for q in cheapest_days],
            "errors": errors,
        }

    # ---- 任意目的地 ----
    async def anywhere(self, origin: str, month: str, trip_days: int | None) -> dict:
        origin = origin.upper()
        if not self.calendar:
            return {"origin": origin, "month": month, "quotes": [], "errors": ["「任意目的地」需要 Travelpayouts token（免费）"]}
        key = f"anywhere:{self.calendar.name}:{origin}:{month}:{trip_days}"
        cached = self.store.cache_get(key)
        if cached is not None:
            quotes = [DateQuote.model_validate(q) for q in cached]
        else:
            quotes = await self.calendar.anywhere(origin, month, trip_days)
            self.store.cache_set(key, [q.model_dump(mode="json") for q in quotes], 360)
        return {"origin": origin, "month": month, "currency": self.settings.currency, "quotes": [q.model_dump(mode="json") for q in quotes[:40]], "errors": []}

    def status(self) -> dict:
        return {
            "demo_mode": self.settings.demo_mode,
            "currency": self.settings.currency,
            "home_airport": self.settings.home_airport,
            "flight_sources": [p.name for p in self.flight_providers],
            "calendar_source": self.calendar.name if self.calendar else None,
            "serpapi_used_this_month": self.store.usage_get("serpapi"),
            "serpapi_monthly_limit": self.settings.serpapi_monthly_limit,
        }
