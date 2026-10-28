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


def test_chinese_place_names_and_multi_city(demo_settings):
    c = client(demo_settings)
    d = (date.today() + timedelta(days=30)).isoformat()
    r = c.get("/api/search", params={"origin": "伦敦", "destination": "贝尔格莱德", "depart_date": d, "airlines": "塞尔维亚航空"}).json()
    assert r["query"]["origin"] == "LON" and r["destination_name"] == "贝尔格莱德"
    assert r["itineraries"] and all(i["airlines"] == ["Air Serbia"] for i in r["itineraries"])
    assert r["airline_summary"][0]["codes"] == ["JU"]
    assert c.get("/api/search", params={"origin": "火星", "destination": "BEG", "depart_date": d}).status_code == 400
    assert c.get("/api/places", params={"q": "贝尔格"}).json()[0]["code"] == "BEG"

    later = (date.today() + timedelta(days=34)).isoformat()
    m = c.post("/api/multi-city", json={"legs": [{"origin": "曼彻斯特", "destination": "巴黎", "date": d}, {"origin": "巴黎", "destination": "罗马", "date": later}]}).json()
    assert m["total_cheapest"] and [l["destination_name"] for l in m["legs"]] == ["巴黎", "罗马"]

    o = c.post("/api/optimize", json={"home": "曼彻斯特", "start": d, "stops": [{"place": "巴黎", "days": 3}, {"place": "罗马", "days": 2}, {"place": "布拉格", "days": 2}]}).json()
    assert o["plans_checked"] == 6 and o["results"][0]["total"] <= o["worst_total"]


def test_lan_access_needs_key(demo_settings):
    app = create_app(demo_settings, scheduler=False, lan_key="secret123")
    pad = TestClient(app, client=("192.168.1.20", 50000))
    assert pad.get("/").status_code == 401
    assert pad.get("/api/status").status_code == 401
    assert pad.get("/static/style.css").status_code == 200
    assert pad.get("/?key=wrong").status_code == 401
    assert pad.get("/?key=secret123").status_code == 200
    assert pad.get("/api/status").status_code == 200  # 之后靠 cookie
    assert TestClient(app).get("/api/status").status_code == 200  # 本机不需要
