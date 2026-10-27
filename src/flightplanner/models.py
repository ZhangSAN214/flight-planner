"""所有数据源统一转换成这里的格式，排序、比价、前端都只认这些模型。"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def normalize_flight_number(v: str) -> str:
    """'ju381' / 'JU 381' / 'JU0381' → 'JU381'"""
    v = v.replace(" ", "").upper()
    if len(v) > 2 and v[2:].isdigit():
        v = v[:2] + str(int(v[2:]))
    return v


class Cabin(str, Enum):
    economy = "economy"
    premium_economy = "premium_economy"
    business = "business"
    first = "first"


class SearchQuery(BaseModel):
    origin: str = Field(min_length=3, max_length=3, description="出发机场或城市 IATA 代码，如 MAN")
    destination: str = Field(min_length=3, max_length=3, description="到达机场或城市 IATA 代码，如 PVG")
    depart_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Cabin = Cabin.economy
    max_stops: int | None = Field(default=None, ge=0, le=3)
    airlines: list[str] = Field(default_factory=list, description="只看这些航司（二字代码）")
    flight_number: str | None = Field(default=None, description="指定航班号，如 JU 381")

    @field_validator("origin", "destination")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("airlines")
    @classmethod
    def upper_list(cls, v: list[str]) -> list[str]:
        return [a.upper() for a in v]

    @field_validator("flight_number")
    @classmethod
    def norm_flight(cls, v: str | None) -> str | None:
        return normalize_flight_number(v) if v else None

    @property
    def round_trip(self) -> bool:
        return self.return_date is not None

    def cache_key(self) -> str:
        return self.model_dump_json()


class Segment(BaseModel):
    carrier_code: str = ""
    carrier_name: str = ""
    flight_number: str = ""
    origin: str
    destination: str
    depart: datetime
    arrive: datetime
    duration_minutes: int


class Slice(BaseModel):
    """一段行程（去程或回程），可能包含多个航段。"""

    segments: list[Segment]
    duration_minutes: int

    @property
    def stops(self) -> int:
        return max(len(self.segments) - 1, 0)


class Itinerary(BaseModel):
    id: str
    source: str
    price: float
    currency: str
    price_display: float | None = None  # 换算成显示货币后的价格
    slices: list[Slice]
    booking_token: str | None = None
    departure_token: str | None = None
    tags: list[str] = Field(default_factory=list)
    score: float | None = None

    @property
    def total_minutes(self) -> int:
        return sum(s.duration_minutes for s in self.slices)

    @property
    def total_stops(self) -> int:
        return sum(s.stops for s in self.slices)

    @property
    def airlines(self) -> list[str]:
        names: list[str] = []
        for s in self.slices:
            for seg in s.segments:
                name = seg.carrier_name or seg.carrier_code
                if name and name not in names:
                    names.append(name)
        return names

    @property
    def carrier_codes(self) -> set[str]:
        return {seg.carrier_code for s in self.slices for seg in s.segments if seg.carrier_code}

    def matches(self, airlines: list[str], flight_number: str | None) -> bool:
        """指定航司时，所有航段都必须是这些航司；指定航班号时，至少一段是这个航班。"""
        if airlines and not (self.carrier_codes and self.carrier_codes <= set(airlines)):
            return False
        if flight_number:
            numbers = {normalize_flight_number(seg.flight_number) for s in self.slices for seg in s.segments if seg.flight_number}
            if flight_number not in numbers:
                return False
        return True

    def fingerprint(self) -> str:
        """同一组航班号 + 起飞时间视为同一行程，用于跨数据源去重。"""
        parts = []
        for s in self.slices:
            for seg in s.segments:
                parts.append(f"{seg.flight_number.replace(' ', '')}@{seg.depart:%Y%m%d%H%M}")
        return "|".join(parts)

    def comparable_price(self) -> float:
        return self.price_display if self.price_display is not None else self.price

    def to_view(self) -> dict:
        data = self.model_dump(mode="json")
        data.update(
            total_minutes=self.total_minutes,
            total_stops=self.total_stops,
            airlines=self.airlines,
            fingerprint=self.fingerprint(),
        )
        for view_slice, s in zip(data["slices"], self.slices):
            view_slice["stops"] = s.stops
        return data


class BookingOption(BaseModel):
    seller: str
    is_airline: bool = False
    price: float | None = None
    currency: str
    fare_name: str = ""
    baggage: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    url: str | None = None
    post_data: str | None = None  # Google 的跳转需要 POST 这段数据
    source: str = ""


class DateQuote(BaseModel):
    depart_date: date
    return_date: date | None = None
    price: float
    currency: str
    source: str
    destination: str | None = None
    airline: str = ""
    transfers: int | None = None


class Deal(BaseModel):
    title: str
    link: str
    published: datetime | None = None
    source: str
    summary: str = ""
    matched: list[str] = Field(default_factory=list)  # 命中的关键字（出发城市等）


class Watch(BaseModel):
    id: int | None = None
    origin: str
    destination: str
    depart_from: date
    depart_to: date
    trip_days: int | None = Field(default=None, ge=1, le=60, description="往返时停留天数；不填为单程")
    target_price: float | None = None
    active: bool = True
    created_at: datetime | None = None
    last_checked: datetime | None = None
    last_price: float | None = None
    last_price_date: date | None = None

    @field_validator("origin", "destination")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()
