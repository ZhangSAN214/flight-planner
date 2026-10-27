"""Travelpayouts（Aviasales）Data API：免费缓存价，用于灵活日期和「任意目的地」。

这里的价格是其他用户近期搜到的缓存价，不是实时价，只用来粗筛。
文档：https://support.travelpayouts.com/hc/en-us/articles/203956163
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from ..models import DateQuote
from .base import PriceCalendarProvider, ProviderError, months_between

BASE = "https://api.travelpayouts.com/aviasales/v3"


class TravelpayoutsProvider(PriceCalendarProvider):
    name = "Travelpayouts（缓存价）"

    def __init__(self, token: str, currency: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self.currency = currency.lower()
        self.client = client or httpx.AsyncClient(timeout=30)

    async def _get(self, path: str, params: dict) -> dict:
        try:
            resp = await self.client.get(
                f"{BASE}/{path}",
                params={**params, "currency": self.currency},
                headers={"X-Access-Token": self.token},
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"Travelpayouts 连接失败：{e}") from e
        if resp.status_code == 401:
            raise ProviderError("Travelpayouts token 无效，请检查 .env 中的 TRAVELPAYOUTS_TOKEN")
        if resp.status_code >= 400:
            raise ProviderError(f"Travelpayouts 返回错误：{resp.text[:200]}")
        data = resp.json()
        if data.get("success") is False:
            raise ProviderError(f"Travelpayouts 返回错误：{data.get('error')}")
        return data

    def _quote(self, row: dict) -> DateQuote | None:
        try:
            dep = datetime.fromisoformat(row["departure_at"]).date()
            ret = datetime.fromisoformat(row["return_at"]).date() if row.get("return_at") else None
            return DateQuote(
                depart_date=dep,
                return_date=ret,
                price=float(row["price"]),
                currency=self.currency.upper(),
                source=self.name,
                destination=row.get("destination"),
                airline=row.get("airline", ""),
                transfers=row.get("transfers"),
            )
        except (KeyError, ValueError, TypeError):
            return None

    async def date_prices(self, origin: str, destination: str, start: date, end: date, trip_days: int | None) -> list[DateQuote]:
        quotes: list[DateQuote] = []
        for month in months_between(start, end):
            params: dict = {"origin": origin, "destination": destination, "departure_at": month, "group_by": "departure_at"}
            if trip_days:
                # 往返：限定停留天数范围（±1 天）
                params["min_trip_duration"] = max(trip_days - 1, 1)
                params["max_trip_duration"] = trip_days + 1
            else:
                params["one_way"] = "true"
            data = await self._get("grouped_prices", params)
            for row in (data.get("data") or {}).values():
                q = self._quote(row)
                if q and start <= q.depart_date <= end:
                    quotes.append(q)
        quotes.sort(key=lambda q: q.depart_date)
        return quotes

    async def anywhere(self, origin: str, month: str, trip_days: int | None) -> list[DateQuote]:
        params: dict = {
            "origin": origin,
            "departure_at": month,
            "sorting": "price",
            "unique": "true",
            "limit": 100,
            "one_way": "false" if trip_days else "true",
        }
        data = await self._get("prices_for_dates", params)
        quotes = [q for row in data.get("data") or [] if (q := self._quote(row))]
        if trip_days:
            quotes = [q for q in quotes if q.return_date and abs((q.return_date - q.depart_date) - timedelta(days=trip_days)).days <= 3]
        quotes.sort(key=lambda q: q.price)
        return quotes
