"""汇率换算：不同数据源可能返回不同货币，统一换成显示货币后再比较。

使用欧洲央行数据的免费接口 frankfurter，无需 key，结果缓存 12 小时。
"""

from __future__ import annotations

import httpx

from .db import Store

FX_URL = "https://api.frankfurter.app/latest"


class FxConverter:
    def __init__(self, store: Store, base: str, client: httpx.AsyncClient | None = None):
        self.store = store
        self.base = base.upper()
        self.client = client or httpx.AsyncClient(timeout=15)

    async def rates(self) -> dict[str, float]:
        key = f"fx:{self.base}"
        cached = self.store.cache_get(key)
        if cached:
            return cached
        try:
            resp = await self.client.get(FX_URL, params={"from": self.base})
            resp.raise_for_status()
            rates = {k.upper(): float(v) for k, v in resp.json().get("rates", {}).items()}
        except (httpx.HTTPError, ValueError):
            return {}
        rates[self.base] = 1.0
        self.store.cache_set(key, rates, minutes=720)
        return rates

    async def convert(self, amount: float, currency: str) -> float | None:
        currency = currency.upper()
        if currency == self.base:
            return amount
        rate = (await self.rates()).get(currency)
        # rates 表示 1 单位基础货币 = rate 单位外币
        return round(amount / rate, 2) if rate else None
