"""SerpApi 的 Google Flights 接口：主数据源，还能拿到各卖家（航司 / OTA）的分别报价。

文档：https://serpapi.com/google-flights-api
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import httpx

from ..db import Store
from ..models import BookingOption, Cabin, Itinerary, SearchQuery, Segment, Slice
from .base import FlightProvider, ProviderError

ENDPOINT = "https://serpapi.com/search.json"
TRAVEL_CLASS = {Cabin.economy: 1, Cabin.premium_economy: 2, Cabin.business: 3, Cabin.first: 4}
# SerpApi 的 stops：0 不限，1 仅直飞，2 最多 1 次，3 最多 2 次
STOPS = {0: 1, 1: 2, 2: 3}


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M")


class SerpApiProvider(FlightProvider):
    name = "Google Flights"

    def __init__(self, api_key: str, store: Store, currency: str, monthly_limit: int, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.store = store
        self.currency = currency
        self.monthly_limit = monthly_limit
        self.client = client or httpx.AsyncClient(timeout=60)

    def _base_params(self, q: SearchQuery) -> dict:
        params = {
            "engine": "google_flights",
            "departure_id": q.origin,
            "arrival_id": q.destination,
            "outbound_date": q.depart_date.isoformat(),
            "type": 1 if q.round_trip else 2,
            "travel_class": TRAVEL_CLASS[q.cabin],
            "adults": q.adults,
            "currency": self.currency,
            "hl": "en",
            "gl": "uk",
        }
        if q.return_date:
            params["return_date"] = q.return_date.isoformat()
        if q.max_stops is not None and q.max_stops in STOPS:
            params["stops"] = STOPS[q.max_stops]
        return params

    async def _call(self, params: dict) -> dict:
        used = self.store.usage_get("serpapi")
        if used >= self.monthly_limit:
            raise ProviderError(f"本月 SerpApi 已用 {used} 次，达到上限 {self.monthly_limit}（可在 .env 调整 SERPAPI_MONTHLY_LIMIT）")
        try:
            resp = await self.client.get(ENDPOINT, params={**params, "api_key": self.api_key})
        except httpx.HTTPError as e:
            raise ProviderError(f"Google Flights 连接失败：{e}") from e
        self.store.usage_add("serpapi")
        if resp.status_code == 401:
            raise ProviderError("SerpApi key 无效，请检查 .env 中的 SERPAPI_KEY")
        if resp.status_code == 429:
            raise ProviderError("SerpApi 本月额度已用完")
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "error" in data:
            msg = data.get("error") or resp.text[:200]
            # 没有结果时 SerpApi 也会返回 error，这种情况当作空结果
            if "hasn't returned any results" in str(msg):
                return {}
            raise ProviderError(f"Google Flights 返回错误：{msg}")
        return data

    async def search(self, query: SearchQuery) -> list[Itinerary]:
        data = await self._call(self._base_params(query))
        items = (data.get("best_flights") or []) + (data.get("other_flights") or [])
        result = []
        for item in items:
            itin = self._to_itinerary(item)
            if itin:
                if query.round_trip:
                    itin.tags.append("round_trip_total")  # 价格为往返总价，此处只列出去程
                result.append(itin)
        return result

    def _to_itinerary(self, item: dict) -> Itinerary | None:
        price = item.get("price")
        flights = item.get("flights") or []
        if price is None or not flights:
            return None
        segments = []
        for f in flights:
            dep, arr = f.get("departure_airport", {}), f.get("arrival_airport", {})
            number = f.get("flight_number", "")
            segments.append(
                Segment(
                    carrier_code=number.split(" ")[0] if number else "",
                    carrier_name=f.get("airline", ""),
                    flight_number=number,
                    origin=dep.get("id", ""),
                    destination=arr.get("id", ""),
                    depart=_parse_time(dep["time"]),
                    arrive=_parse_time(arr["time"]),
                    duration_minutes=int(f.get("duration") or 0),
                )
            )
        total = int(item.get("total_duration") or sum(s.duration_minutes for s in segments))
        token = item.get("booking_token") or item.get("departure_token") or ""
        return Itinerary(
            id="gf-" + hashlib.sha1((token or json.dumps(item, sort_keys=True)).encode()).hexdigest()[:12],
            source=self.name,
            price=float(price),
            currency=self.currency,
            slices=[Slice(segments=segments, duration_minutes=total)],
            booking_token=item.get("booking_token"),
            departure_token=item.get("departure_token"),
        )

    async def booking_options(self, itinerary: Itinerary, query: SearchQuery) -> list[BookingOption]:
        token = itinerary.booking_token
        if not token and itinerary.departure_token:
            # 往返：先用 departure_token 取回程列表，选第一个（Google 推荐的）回程拿 booking_token
            returns = await self._call({**self._base_params(query), "departure_token": itinerary.departure_token})
            options = (returns.get("best_flights") or []) + (returns.get("other_flights") or [])
            token = next((o.get("booking_token") for o in options if o.get("booking_token")), None)
        if not token:
            return []
        data = await self._call({**self._base_params(query), "booking_token": token})
        result: list[BookingOption] = []
        for opt in data.get("booking_options") or []:
            # 一张票买齐用 together；分开出票时有 departing / returning 两张
            parts = [opt["together"]] if "together" in opt else [opt[k] for k in ("departing", "returning") if k in opt]
            if not parts:
                continue
            first = parts[0]
            prices = [p.get("price") for p in parts]
            total = sum(prices) if all(p is not None for p in prices) else None
            req = first.get("booking_request") or {}
            seller = " + ".join(dict.fromkeys(p.get("book_with", "") for p in parts))
            result.append(
                BookingOption(
                    seller=seller + ("（分开出票）" if len(parts) > 1 else ""),
                    is_airline=bool(first.get("airline")),
                    price=float(total) if total is not None else None,
                    currency=self.currency,
                    fare_name=first.get("option_title", ""),
                    baggage=list(first.get("baggage_prices") or []),
                    conditions=list(first.get("extensions") or []),
                    url=req.get("url"),
                    post_data=req.get("post_data"),
                    source=self.name,
                )
            )
        return result
