"""数据源接口。新增数据源时实现这两个类之一，排序和前端都不用改。"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date

from ..models import BookingOption, DateQuote, Itinerary, SearchQuery


class ProviderError(Exception):
    """数据源出错（无 key、超额、网络失败等），信息会显示给用户。"""


class FlightProvider(ABC):
    """返回实时航班报价的数据源。"""

    name: str = "base"

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[Itinerary]: ...

    async def booking_options(self, itinerary: Itinerary, query: SearchQuery) -> list[BookingOption]:
        """同一行程在各家卖家（航司官网、OTA）的价格。不支持时返回空列表。"""
        return []


class PriceCalendarProvider(ABC):
    """返回缓存价（非实时）的数据源，用于灵活日期和任意目的地粗筛。"""

    name: str = "base"

    @abstractmethod
    async def date_prices(
        self, origin: str, destination: str, start: date, end: date, trip_days: int | None
    ) -> list[DateQuote]: ...

    @abstractmethod
    async def anywhere(self, origin: str, month: str, trip_days: int | None) -> list[DateQuote]: ...


_ISO_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?")


def iso_duration_minutes(value: str | None) -> int:
    """'PT13H5M' / 'P1DT2H' → 分钟。"""
    if not value:
        return 0
    m = _ISO_DURATION.fullmatch(value)
    if not m:
        return 0
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return d * 1440 + h * 60 + mi


def months_between(start: date, end: date) -> list[str]:
    months: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months
