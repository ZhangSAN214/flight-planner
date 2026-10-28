from datetime import date, timedelta

import httpx
import pytest
import respx

from flightplanner.models import DateQuote, Itinerary, SearchQuery, Segment, Slice
from flightplanner.places import UnknownPlace, airports_for, parse_airlines, resolve, same_place, search_places
from flightplanner.providers import FlightProvider, PriceCalendarProvider, ProviderError
from flightplanner.providers.serpapi import SerpApiProvider
from flightplanner.service import Planner
from flightplanner.trips import Leg, Stop, multi_leg, optimize_order
from flightplanner.scoring import Preferences


@pytest.mark.parametrize(
    "text,code",
    [("伦敦", "LON"), ("london", "LON"), ("贝尔格莱德", "BEG"), ("Belgrade", "BEG"), ("lhr", "LHR"), ("伦敦希思罗", "LHR"),
     ("上海浦东", "PVG"), ("Zürich", "ZRH"), ("München", "MUC"), ("曼城", "MAN"), ("XYZ", "XYZ")],
)
def test_resolve(text, code):
    assert resolve(text) == code


def test_resolve_unknown_suggests():
    with pytest.raises(UnknownPlace) as e:
        resolve("贝尔")
    assert any(p.code == "BEG" for p in e.value.suggestions)
    with pytest.raises(UnknownPlace):
        resolve("火星")


def test_airports_and_same_place():
    assert airports_for("LON")[:2] == ["LHR", "LGW"]
    assert airports_for("BEG") == ["BEG"]
    assert airports_for("SHA") == ["PVG", "SHA"]
    assert same_place("LON", "LGW") and not same_place("LON", "MAN")
    assert [p.code for p in search_places("bel")] == ["BFS", "BEG"]


def test_parse_airlines():
    assert parse_airlines("英航, JU 汉莎") == ["BA", "JU", "LH"]
    assert parse_airlines("Air Serbia") == ["JU"]
    with pytest.raises(ValueError):
        parse_airlines("不存在的航空")


def _itin(id, codes, numbers):
    t = date(2026, 12, 1)
    segs = [
        Segment(carrier_code=c, flight_number=n, origin="A", destination="B", depart=f"{t}T10:00", arrive=f"{t}T12:00", duration_minutes=120)
        for c, n in zip(codes, numbers)
    ]
    return Itinerary(id=id, source="x", price=100, currency="GBP", slices=[Slice(segments=segs, duration_minutes=120 * len(segs))])


def test_itinerary_matches():
    ju = _itin("a", ["JU"], ["JU 381"])
    mixed = _itin("b", ["JU", "LH"], ["JU 1", "LH 2"])
    assert ju.matches(["JU"], None) and not mixed.matches(["JU"], None)
    assert mixed.matches(["JU", "LH"], None)
    assert ju.matches([], "JU381") and ju.matches([], SearchQuery(origin="LON", destination="BEG", depart_date=date(2026, 12, 1), flight_number="ju 0381").flight_number)
    assert not ju.matches([], "JU382")


@respx.mock
async def test_serpapi_expands_city_and_airline(store):
    route = respx.get("https://serpapi.com/search.json").mock(return_value=httpx.Response(200, json={}))
    p = SerpApiProvider("key", store, "GBP", 10)
    await p.search(SearchQuery(origin="LON", destination="BEG", depart_date=date(2026, 12, 1), flight_number="JU 381"))
    params = route.calls[0].request.url.params
    assert params["departure_id"] == "LHR,LGW,STN,LTN,LCY,SEN"
    assert params["arrival_id"] == "BEG"
    assert params["include_airlines"] == "JU"


class FixedFlights(FlightProvider):
    """每条航线一个固定价格的单程航班。"""

    name = "fixed"

    def __init__(self, prices):
        self.prices = prices
        self.calls = 0

    async def search(self, q):
        self.calls += 1
        p = self.prices.get((q.origin, q.destination))
        if p is None:
            return []
        t = q.depart_date
        seg = Segment(carrier_code="XX", flight_number=f"XX {self.calls}", origin=q.origin, destination=q.destination,
                      depart=f"{t}T10:00", arrive=f"{t}T12:00", duration_minutes=120)
        return [Itinerary(id=f"{q.origin}{q.destination}{t}", source=self.name, price=p, currency="GBP", slices=[Slice(segments=[seg], duration_minutes=120)])]


class FixedCalendar(PriceCalendarProvider):
    name = "fixedcal"

    def __init__(self, prices):
        self.prices = prices

    async def date_prices(self, origin, destination, start, end, trip_days):
        p = self.prices.get((origin, destination))
        if p is None:
            return []
        return [DateQuote(depart_date=start + timedelta(days=i), price=p, currency="GBP", source=self.name) for i in range((end - start).days + 1)]

    async def anywhere(self, origin, month, trip_days):
        return []


# 设计成 MAN→PAR→ROM→MAN 最便宜：50 + 40 + 60 = 150
PRICES = {
    ("MAN", "PAR"): 50, ("PAR", "ROM"): 40, ("ROM", "MAN"): 60,
    ("MAN", "ROM"): 200, ("ROM", "PAR"): 200, ("PAR", "MAN"): 200,
}


async def test_optimize_with_calendar(store, demo_settings):
    planner = Planner(demo_settings, store, flight_providers=[FixedFlights(PRICES)], calendar=FixedCalendar(PRICES))
    start = date.today() + timedelta(days=20)
    res = await optimize_order(planner, "MAN", [Stop(place="ROM", days=3), Stop(place="PAR", days=2)], start)
    best = res["results"][0]
    assert best["order_codes"] == ["PAR", "ROM"] and best["total"] == 150
    assert [l["date"] for l in best["legs"]] == [str(start), str(start + timedelta(days=2)), str(start + timedelta(days=5))]
    assert res["worst_total"] == 600 and res["price_type"] == "缓存价估算"


async def test_optimize_live_respects_call_limit(store, demo_settings):
    flights = FixedFlights(PRICES)
    planner = Planner(demo_settings, store, flight_providers=[flights], calendar=None)
    start = date.today() + timedelta(days=20)
    res = await optimize_order(planner, "MAN", [Stop(place="ROM", days=3), Stop(place="PAR", days=2)], start, live=True)
    assert res["results"][0]["total"] == 150 and res["searches_used"] == 6
    with pytest.raises(ProviderError, match="超过上限"):
        await optimize_order(planner, "MAN", [Stop(place="ROM", days=3), Stop(place="PAR", days=2)], start, live=True, max_live_calls=3)


async def test_optimize_validation(store, demo_settings):
    planner = Planner(demo_settings, store, flight_providers=[], calendar=FixedCalendar(PRICES))
    start = date.today() + timedelta(days=5)
    with pytest.raises(ProviderError, match="出发地"):
        await optimize_order(planner, "LON", [Stop(place="LHR", days=1), Stop(place="PAR", days=1)], start)
    with pytest.raises(ProviderError, match="2 到"):
        await optimize_order(planner, "MAN", [Stop(place="PAR", days=1)], start)


async def test_multi_leg_totals(store, demo_settings):
    planner = Planner(demo_settings, store, flight_providers=[FixedFlights(PRICES)], calendar=None)
    d = date.today() + timedelta(days=10)
    res = await multi_leg(planner, [Leg(origin="MAN", destination="PAR", date=d), Leg(origin="PAR", destination="ROM", date=d + timedelta(days=2))], Preferences())
    assert res["total_cheapest"] == 90 and len(res["legs"]) == 2
    with pytest.raises(ProviderError, match="早于"):
        await multi_leg(planner, [Leg(origin="MAN", destination="PAR", date=d), Leg(origin="PAR", destination="ROM", date=d - timedelta(days=1))], Preferences())


def test_country_name_asks_for_city():
    import pytest

    from flightplanner.places import UnknownPlace, resolve

    with pytest.raises(UnknownPlace) as e:
        resolve("英国")
    assert "伦敦" in str(e.value)
    assert e.value.suggestions[0].code == "LON"
