from datetime import date, timedelta

from fastapi.testclient import TestClient

from flightplanner.api import create_app


def client(settings):
    return TestClient(create_app(settings, scheduler=False))


def test_demo_end_to_end(demo_settings):
    c = client(demo_settings)
    s = c.get("/api/status").json()
    assert s["demo_mode"] and s["flight_sources"] == ["演示数据"]

    d = (date.today() + timedelta(days=30)).isoformat()
    r = c.get("/api/search", params={"origin": "man", "destination": "pvg", "depart_date": d, "mode": "cheapest"})
    assert r.status_code == 200
    data = r.json()
    prices = [i["price_display"] for i in data["itineraries"]]
    assert prices == sorted(prices) and len(prices) > 5
    assert any("best" in i["tags"] for i in data["itineraries"])
    assert {l["name"] for l in data["links"]} >= {"Google Flights", "Skyscanner", "携程（国内版）"}

    opts = c.get(f"/api/booking-options/{data['itineraries'][0]['id']}").json()
    assert len(opts["options"]) == 4 and opts["options"][0]["cheapest"]

    flex = c.get("/api/flexible", params={"origin": "MAN", "destination": "PVG", "start": d, "end": (date.today() + timedelta(days=60)).isoformat(), "trip_days": 10}).json()
    assert len(flex["quotes"]) == 31 and len(flex["cheapest"]) == 5

    any_ = c.get("/api/anywhere", params={"month": d[:7]}).json()
    assert any_["origin"] == "MAN" and any_["quotes"]

    w = c.post("/api/watches", json={"origin": "man", "destination": "pvg", "depart_from": d, "depart_to": d, "target_price": 99999}).json()
    assert w["first_check"]["status"] == "ok"
    wid = w["watch"]["id"]
    assert c.post(f"/api/watches/{wid}/check").json()["alert"]
    assert c.get(f"/api/watches/{wid}/history").json()
    assert c.delete(f"/api/watches/{wid}").status_code == 200
    assert c.get("/api/watches").json() == []


def test_validation(demo_settings):
    c = client(demo_settings)
    r = c.get("/api/search", params={"origin": "MAN", "destination": "PVG", "depart_date": "2026-12-10", "return_date": "2026-12-01"})
    assert r.status_code == 400
    assert c.get("/api/booking-options/nope").status_code == 400
    assert c.get("/api/flexible", params={"origin": "MAN", "destination": "PVG", "start": "2026-12-10", "end": "2026-12-01"}).status_code == 400


def test_index_served(demo_settings):
    c = client(demo_settings)
    assert "航班规划" in c.get("/").text
    assert c.get("/static/app.js").status_code == 200
