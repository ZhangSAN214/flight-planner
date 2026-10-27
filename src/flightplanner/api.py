"""网页后端（FastAPI）。启动：python -m flightplanner 或 flightplanner serve"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings, load_settings
from .db import Store
from .deals.feeds import fetch_deals
from .deals.notify import channels, notify
from .deals.watcher import check_watch, run_scheduler
from .models import Cabin, SearchQuery, Watch
from .places import UnknownPlace, parse_airlines, resolve, search_places
from .providers import ProviderError
from .scoring import Mode, Preferences
from .service import Planner
from .trips import Leg, Stop, multi_leg, optimize_order

WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger(__name__)


class LegIn(BaseModel):
    origin: str
    destination: str
    date: date
    flight_number: str | None = None


class MultiCityIn(BaseModel):
    legs: list[LegIn]
    mode: Mode = Mode.best
    hour_value: float = 15.0
    adults: int = 1
    cabin: Cabin = Cabin.economy
    max_stops: int | None = None
    airlines: str | None = None


class StopIn(BaseModel):
    place: str
    days: int


class OptimizeIn(BaseModel):
    home: str
    stops: list[StopIn]
    start: date
    return_home: bool = True
    start_flex_days: int = 0
    live: bool = False


class WatchIn(BaseModel):
    origin: str
    destination: str
    depart_from: date
    depart_to: date
    trip_days: int | None = None
    target_price: float | None = None


def create_app(settings: Settings | None = None, planner: Planner | None = None, scheduler: bool = True) -> FastAPI:
    settings = settings or load_settings()
    store = planner.store if planner else Store(settings.db_path)
    planner = planner or Planner(settings, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(run_scheduler(planner, store)) if scheduler else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="航班规划与特价雷达", lifespan=lifespan)
    app.state.planner = planner
    app.state.store = store

    def bad_request(e: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(e))

    def place(text: str) -> str:
        try:
            return resolve(text)
        except UnknownPlace as e:
            raise bad_request(e)

    def airlines_of(text: str | None) -> list[str]:
        try:
            return parse_airlines(text)
        except ValueError as e:
            raise bad_request(e)

    @app.get("/api/places")
    async def places(q: str = ""):
        return [p.to_view() for p in search_places(q)]

    @app.get("/api/status")
    async def status():
        return {**planner.status(), "alert_channels": channels(settings), "deal_feeds": settings.deal_feeds}

    @app.get("/api/search")
    async def search(
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date | None = None,
        adults: int = 1,
        cabin: Cabin = Cabin.economy,
        max_stops: int | None = None,
        mode: Mode = Mode.best,
        hour_value: float = Query(15.0, ge=0, le=500),
        stop_penalty: float = Query(25.0, ge=0, le=1000),
        airlines: str | None = None,
        flight_number: str | None = None,
    ):
        try:
            q = SearchQuery(
                origin=place(origin), destination=place(destination), depart_date=depart_date, return_date=return_date,
                adults=adults, cabin=cabin, max_stops=max_stops, airlines=airlines_of(airlines), flight_number=flight_number or None,
            )
        except ValueError as e:
            raise bad_request(e)
        if q.return_date and q.return_date < q.depart_date:
            raise HTTPException(400, "返程日期早于出发日期")
        result = await planner.search(q, Preferences(mode=mode, hour_value=hour_value, stop_penalty=stop_penalty))
        return result.to_view()

    @app.get("/api/booking-options/{itinerary_id}")
    async def booking_options(itinerary_id: str):
        try:
            return await planner.booking_options(itinerary_id)
        except ProviderError as e:
            raise bad_request(e)

    @app.get("/api/flexible")
    async def flexible(origin: str, destination: str, start: date, end: date, trip_days: int | None = None):
        try:
            return await planner.flexible(place(origin), place(destination), start, end, trip_days)
        except ProviderError as e:
            raise bad_request(e)

    @app.get("/api/anywhere")
    async def anywhere(origin: str | None = None, month: str = Query(..., pattern=r"^\d{4}-\d{2}$"), trip_days: int | None = None):
        try:
            return await planner.anywhere(place(origin) if origin else settings.home_airport, month, trip_days)
        except ProviderError as e:
            raise bad_request(e)

    @app.post("/api/multi-city")
    async def multi_city(body: MultiCityIn):
        legs = [Leg(origin=place(l.origin), destination=place(l.destination), date=l.date, flight_number=(l.flight_number or "").strip() or None) for l in body.legs]
        try:
            return await multi_leg(
                planner, legs, Preferences(mode=body.mode, hour_value=body.hour_value),
                adults=body.adults, cabin=body.cabin, max_stops=body.max_stops, airlines=airlines_of(body.airlines),
            )
        except ProviderError as e:
            raise bad_request(e)

    @app.post("/api/optimize")
    async def optimize(body: OptimizeIn):
        try:
            stops = [Stop(place=place(s.place), days=s.days) for s in body.stops]
        except ValueError as e:
            raise bad_request(e)
        try:
            return await optimize_order(planner, place(body.home), stops, body.start, body.return_home, body.start_flex_days, body.live)
        except ProviderError as e:
            raise bad_request(e)

    @app.get("/api/deals")
    async def deals(airports: str | None = None):
        codes = [a.strip().upper() for a in (airports or settings.home_airport).split(",") if a.strip()]
        items, errors = await fetch_deals(settings.deal_feeds, codes)
        return {"deals": [d.model_dump(mode="json") for d in items[:60]], "errors": errors}

    @app.get("/api/watches")
    async def list_watches():
        return [w.model_dump(mode="json") for w in store.watch_list()]

    @app.post("/api/watches")
    async def add_watch(body: WatchIn):
        try:
            w = Watch(**{**body.model_dump(), "origin": place(body.origin), "destination": place(body.destination)})
        except ValueError as e:
            raise bad_request(e)
        if w.depart_to < w.depart_from:
            raise HTTPException(400, "结束日期早于开始日期")
        if (w.depart_to - w.depart_from).days > 92:
            raise HTTPException(400, "监控日期范围最多 3 个月")
        w = store.watch_add(w)
        first = await check_watch(planner, store, w, send=False)
        return {"watch": (store.watch_get(w.id) or w).model_dump(mode="json"), "first_check": first}

    @app.delete("/api/watches/{watch_id}")
    async def delete_watch(watch_id: int):
        if not store.watch_delete(watch_id):
            raise HTTPException(404, "没有这个监控")
        return {"ok": True}

    @app.post("/api/watches/{watch_id}/check")
    async def check_now(watch_id: int):
        w = store.watch_get(watch_id)
        if not w:
            raise HTTPException(404, "没有这个监控")
        return await check_watch(planner, store, w)

    @app.get("/api/watches/{watch_id}/history")
    async def history(watch_id: int):
        return store.history_list(watch_id)

    @app.post("/api/test-alert")
    async def test_alert():
        sent = await notify(settings, "航班规划：测试提醒", "如果你收到这条消息，说明提醒设置成功。")
        return {"sent": sent, "configured": channels(settings)}

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
