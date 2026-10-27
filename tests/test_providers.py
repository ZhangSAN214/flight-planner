import json
from datetime import date

import httpx
import pytest
import respx

from flightplanner.models import SearchQuery
from flightplanner.providers import ProviderError
from flightplanner.providers.base import iso_duration_minutes, months_between
from flightplanner.providers.duffel import DuffelProvider
from flightplanner.providers.serpapi import SerpApiProvider
from flightplanner.providers.travelpayouts import TravelpayoutsProvider

SERP_SEARCH = {
    "best_flights": [
        {
            "flights": [
                {"departure_airport": {"id": "MAN", "time": "2026-12-01 10:05"}, "arrival_airport": {"id": "DXB", "time": "2026-12-01 20:55"},
                 "duration": 410, "airline": "Emirates", "flight_number": "EK 18"},
                {"departure_airport": {"id": "DXB", "time": "2026-12-02 03:00"}, "arrival_airport": {"id": "PVG", "time": "2026-12-02 14:20"},
                 "duration": 500, "airline": "Emirates", "flight_number": "EK 302"},
            ],
            "layovers": [{"duration": 365, "id": "DXB"}],
            "total_duration": 1275,
            "price": 512,
            "booking_token": "tok-1",
        }
    ],
    "other_flights": [
        {"flights": [{"departure_airport": {"id": "MAN", "time": "2026-12-01 07:00"}, "arrival_airport": {"id": "PVG", "time": "2026-12-02 01:00"},
                      "duration": 720, "airline": "Fake Air", "flight_number": "FA 1"}],
         "total_duration": 720, "price": 700, "booking_token": "tok-2"},
        {"flights": [], "price": 10},
    ],
}
SERP_BOOKING = {
    "booking_options": [
        {"together": {"book_with": "Emirates", "airline": True, "price": 512, "option_title": "Economy Saver",
                      "baggage_prices": ["1 free carry-on"], "booking_request": {"url": "https://www.google.com/travel/clk/f", "post_data": "u=abc&x=1"}}},
        {"together": {"book_with": "Trip.com", "airline": False, "price": 489, "booking_request": {"url": "https://www.google.com/travel/clk/f", "post_data": "u=def"}}},
        {"departing": {"book_with": "Kiwi.com", "price": 200}, "returning": {"book_with": "Kiwi.com", "price": 230}},
    ]
}


def q(**kw):
    return SearchQuery(origin="man", destination="pvg", depart_date=date(2026, 12, 1), **kw)


@respx.mock
async def test_serpapi_search_and_booking(store):
    route = respx.get("https://serpapi.com/search.json").mock(
        side_effect=lambda req: httpx.Response(200, json=SERP_BOOKING if "booking_token" in req.url.params else SERP_SEARCH)
    )
    p = SerpApiProvider("key", store, "GBP", monthly_limit=10)
    itins = await p.search(q(max_stops=1))
    assert len(itins) == 2
    first = itins[0]
    assert first.price == 512 and first.total_stops == 1 and first.total_minutes == 1275
    assert first.slices[0].segments[1].flight_number == "EK 302"
    assert route.calls[0].request.url.params["type"] == "2"
    assert route.calls[0].request.url.params["stops"] == "2"
    opts = await p.booking_options(first, q())
    assert [o.seller for o in opts] == ["Emirates", "Trip.com", "Kiwi.com（分开出票）"]
    assert opts[0].is_airline and opts[0].post_data == "u=abc&x=1"
    assert opts[2].price == 430
    assert store.usage_get("serpapi") == 2


@respx.mock
async def test_serpapi_monthly_limit(store):
    respx.get("https://serpapi.com/search.json").mock(return_value=httpx.Response(200, json=SERP_SEARCH))
    p = SerpApiProvider("key", store, "GBP", monthly_limit=1)
    await p.search(q())
    with pytest.raises(ProviderError, match="上限"):
        await p.search(q())


@respx.mock
async def test_serpapi_no_results_is_empty(store):
    respx.get("https://serpapi.com/search.json").mock(
        return_value=httpx.Response(200, json={"error": "Google Flights hasn't returned any results for this query."})
    )
    assert await SerpApiProvider("key", store, "GBP", 10).search(q()) == []


@respx.mock
async def test_serpapi_round_trip_booking_uses_departure_token(store):
    outbound = {"best_flights": [{**SERP_SEARCH["best_flights"][0], "booking_token": None, "departure_token": "dep-1"}]}
    returns = {"best_flights": [{**SERP_SEARCH["other_flights"][0], "booking_token": "ret-tok"}]}

    def handler(req):
        params = req.url.params
        if "booking_token" in params:
            assert params["booking_token"] == "ret-tok"
            return httpx.Response(200, json=SERP_BOOKING)
        if "departure_token" in params:
            return httpx.Response(200, json=returns)
        return httpx.Response(200, json=outbound)

    respx.get("https://serpapi.com/search.json").mock(side_effect=handler)
    p = SerpApiProvider("key", store, "GBP", 10)
    query = q(return_date=date(2026, 12, 15))
    itins = await p.search(query)
    assert "round_trip_total" in itins[0].tags
    assert len(await p.booking_options(itins[0], query)) == 3


DUFFEL = {
    "data": {
        "offers": [
            {
                "id": "off_1", "total_amount": "455.20", "total_currency": "EUR",
                "slices": [{
                    "duration": "PT14H30M",
                    "segments": [
                        {"origin": {"iata_code": "MAN"}, "destination": {"iata_code": "AMS"}, "departing_at": "2026-12-01T06:00:00",
                         "arriving_at": "2026-12-01T08:20:00", "duration": "PT1H20M", "marketing_carrier": {"iata_code": "KL", "name": "KLM"},
                         "marketing_carrier_flight_number": "1070"},
                        {"origin": {"iata_code": "AMS"}, "destination": {"iata_code": "PVG"}, "departing_at": "2026-12-01T10:00:00",
                         "arriving_at": "2026-12-02T03:30:00", "duration": "PT10H30M", "marketing_carrier": {"iata_code": "KL", "name": "KLM"},
                         "marketing_carrier_flight_number": "895"},
                    ],
                }],
            },
            {"id": "broken", "total_amount": "1"},
        ]
    }
}


@respx.mock
async def test_duffel_search():
    route = respx.post("https://api.duffel.com/air/offer_requests").mock(return_value=httpx.Response(201, json=DUFFEL))
    itins = await DuffelProvider("tok").search(q(adults=2, max_stops=1))
    assert len(itins) == 1
    it = itins[0]
    assert it.price == 455.2 and it.currency == "EUR" and it.total_minutes == 870
    assert it.slices[0].segments[0].flight_number == "KL 1070"
    body = json.loads(route.calls[0].request.content)
    assert len(body["data"]["passengers"]) == 2 and body["data"]["max_connections"] == 1
    assert route.calls[0].request.headers["Duffel-Version"] == "v2"


@respx.mock
async def test_duffel_error_message():
    respx.post("https://api.duffel.com/air/offer_requests").mock(
        return_value=httpx.Response(422, json={"errors": [{"message": "Invalid airport"}]})
    )
    with pytest.raises(ProviderError, match="Invalid airport"):
        await DuffelProvider("tok").search(q())


@respx.mock
async def test_travelpayouts_calendar_and_anywhere():
    grouped = {"success": True, "currency": "gbp", "data": {
        "2026-12-02": {"origin": "MAN", "destination": "PVG", "price": 420, "airline": "EK", "departure_at": "2026-12-02T10:00:00+00:00", "transfers": 1},
        "2026-11-20": {"origin": "MAN", "destination": "PVG", "price": 380, "airline": "EK", "departure_at": "2026-11-20T10:00:00+00:00", "transfers": 1},
    }}
    anywhere = {"success": True, "data": [
        {"origin": "MAN", "destination": "BCN", "price": 60, "departure_at": "2026-12-05T08:00:00+01:00", "return_at": "2026-12-09T08:00:00+01:00", "transfers": 0},
        {"origin": "MAN", "destination": "DUB", "price": 30, "departure_at": "2026-12-05T08:00:00+01:00", "return_at": "2026-12-25T08:00:00+01:00", "transfers": 0},
    ]}
    respx.get("https://api.travelpayouts.com/aviasales/v3/grouped_prices").mock(return_value=httpx.Response(200, json=grouped))
    respx.get("https://api.travelpayouts.com/aviasales/v3/prices_for_dates").mock(return_value=httpx.Response(200, json=anywhere))
    tp = TravelpayoutsProvider("tok", "GBP")
    quotes = await tp.date_prices("MAN", "PVG", date(2026, 12, 1), date(2026, 12, 31), None)
    assert [x.depart_date for x in quotes] == [date(2026, 12, 2)]  # 范围外的日期被过滤
    res = await tp.anywhere("MAN", "2026-12", trip_days=4)
    assert [x.destination for x in res] == ["BCN"]  # 停留 20 天的被过滤


def test_helpers():
    assert iso_duration_minutes("PT13H5M") == 785
    assert iso_duration_minutes("P1DT2H") == 1560
    assert iso_duration_minutes(None) == 0
    assert months_between(date(2026, 11, 20), date(2027, 1, 3)) == ["2026-11", "2026-12", "2027-01"]
