"""Duffel：航司直连（NDC）实时报价，第二数据源。

文档：https://duffel.com/docs/api/v2/offer-requests
"""

from __future__ import annotations

from datetime import datetime

import httpx

from ..models import BookingOption, Itinerary, SearchQuery, Segment, Slice
from .base import FlightProvider, ProviderError, iso_duration_minutes

ENDPOINT = "https://api.duffel.com/air/offer_requests"


class DuffelProvider(FlightProvider):
    name = "Duffel"

    def __init__(self, token: str, client: httpx.AsyncClient | None = None, max_offers: int = 60):
        self.token = token
        self.client = client or httpx.AsyncClient(timeout=90)
        self.max_offers = max_offers

    async def search(self, query: SearchQuery) -> list[Itinerary]:
        slices = [{"origin": query.origin, "destination": query.destination, "departure_date": query.depart_date.isoformat()}]
        if query.return_date:
            slices.append({"origin": query.destination, "destination": query.origin, "departure_date": query.return_date.isoformat()})
        body: dict = {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"} for _ in range(query.adults)],
                "cabin_class": query.cabin.value,
            }
        }
        if query.max_stops is not None:
            body["data"]["max_connections"] = query.max_stops
        try:
            resp = await self.client.post(
                ENDPOINT,
                params={"return_offers": "true"},
                json=body,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Duffel-Version": "v2",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"Duffel 连接失败：{e}") from e
        if resp.status_code == 401:
            raise ProviderError("Duffel token 无效，请检查 .env 中的 DUFFEL_TOKEN")
        if resp.status_code >= 400:
            try:
                errors = resp.json().get("errors") or []
                msg = "; ".join(e.get("message", "") for e in errors) or resp.text[:200]
            except ValueError:
                msg = resp.text[:200]
            raise ProviderError(f"Duffel 返回错误：{msg}")
        offers = (resp.json().get("data") or {}).get("offers") or []
        offers.sort(key=lambda o: float(o.get("total_amount") or 1e12))
        return [it for o in offers[: self.max_offers] if (it := self._to_itinerary(o))]

    def _to_itinerary(self, offer: dict) -> Itinerary | None:
        try:
            slices = []
            for s in offer["slices"]:
                segments = []
                for seg in s["segments"]:
                    carrier = seg.get("marketing_carrier") or {}
                    code = carrier.get("iata_code", "")
                    segments.append(
                        Segment(
                            carrier_code=code,
                            carrier_name=carrier.get("name", ""),
                            flight_number=f"{code} {seg.get('marketing_carrier_flight_number', '')}".strip(),
                            origin=seg["origin"]["iata_code"],
                            destination=seg["destination"]["iata_code"],
                            depart=datetime.fromisoformat(seg["departing_at"]),
                            arrive=datetime.fromisoformat(seg["arriving_at"]),
                            duration_minutes=iso_duration_minutes(seg.get("duration")),
                        )
                    )
                duration = iso_duration_minutes(s.get("duration"))
                if not duration and segments:
                    # 没有时长时用首末时间估算（未计时区差，仅兜底）
                    duration = int((segments[-1].arrive - segments[0].depart).total_seconds() // 60)
                slices.append(Slice(segments=segments, duration_minutes=duration))
            return Itinerary(
                id="df-" + offer["id"],
                source=self.name,
                price=float(offer["total_amount"]),
                currency=offer["total_currency"],
                slices=slices,
            )
        except (KeyError, ValueError, TypeError):
            return None

    async def booking_options(self, itinerary: Itinerary, query: SearchQuery) -> list[BookingOption]:
        # Duffel 报价来自航司本身，作为一个「航司直连」卖家展示
        owner = itinerary.airlines[0] if itinerary.airlines else "航司"
        return [
            BookingOption(
                seller=f"{owner}（Duffel 直连价）",
                is_airline=True,
                price=itinerary.price,
                currency=itinerary.currency,
                source=self.name,
            )
        ]
