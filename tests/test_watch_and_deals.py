from datetime import date, timedelta

from flightplanner.deals.feeds import extract_price, keywords_for, match, parse_feed
from flightplanner.deals.watcher import check_watch, should_alert
from flightplanner.models import DateQuote, Watch
from flightplanner.providers import PriceCalendarProvider
from flightplanner.service import Planner


def test_should_alert():
    assert should_alert(90, 100, None, 0).startswith("低于目标价")
    assert should_alert(110, 100, None, 0) is None
    assert should_alert(65, None, 100, 3) is not None
    assert should_alert(65, None, 100, 2) is None  # 历史不足
    assert should_alert(75, None, 100, 5) is None


class FakeCalendar(PriceCalendarProvider):
    name = "fake"

    def __init__(self):
        self.price = 400.0

    async def date_prices(self, origin, destination, start, end, trip_days):
        return [DateQuote(depart_date=start + timedelta(days=i), price=self.price + i, currency="GBP", source="fake") for i in range((end - start).days + 1)]

    async def anywhere(self, origin, month, trip_days):
        return []


async def test_watch_alerts_on_price_drop(store, demo_settings, monkeypatch):
    cal = FakeCalendar()
    demo_settings.cache_minutes = 0
    planner = Planner(demo_settings, store, flight_providers=[], calendar=cal)
    sent = []

    async def fake_notify(settings, subject, body):
        sent.append(subject)
        return ["test"]

    monkeypatch.setattr("flightplanner.deals.watcher.notify", fake_notify)
    monkeypatch.setattr("flightplanner.service.Planner.flexible", _uncached_flexible(Planner.flexible, store))
    start = date.today() + timedelta(days=10)
    w = store.watch_add(Watch(origin="MAN", destination="PVG", depart_from=start, depart_to=start + timedelta(days=2)))
    for _ in range(3):
        r = await check_watch(planner, store, w)
        assert r["status"] == "ok" and r["alert"] is None
        store._conn.execute("UPDATE price_history SET checked_at = datetime(checked_at, '-1 day')")
    cal.price = 250
    r = await check_watch(planner, store, w)
    assert r["alert"] and "低" in r["alert"] and r["best_price"] == 250
    assert len(sent) == 1
    # 同样的价格不重复提醒
    r = await check_watch(planner, store, w)
    assert len(sent) == 1
    assert store.watch_get(w.id).last_price == 250


def _uncached_flexible(original, store):
    async def wrapper(self, *args, **kwargs):
        store._conn.execute("DELETE FROM cache WHERE key LIKE 'cal:%'")
        return await original(self, *args, **kwargs)
    return wrapper


async def test_target_price_alert(store, demo_settings, monkeypatch):
    planner = Planner(demo_settings, store, flight_providers=[], calendar=FakeCalendar())
    monkeypatch.setattr("flightplanner.deals.watcher.notify", lambda *a: _async([]))
    start = date.today() + timedelta(days=5)
    w = store.watch_add(Watch(origin="MAN", destination="PVG", depart_from=start, depart_to=start, target_price=450))
    r = await check_watch(planner, store, w)
    assert r["alert"].startswith("低于目标价")


async def _async(v):
    return v


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Deals</title>
<item><title>Manchester, UK to Shanghai, China for only \xc2\xa3299 roundtrip</title><link>https://example.com/a</link>
<pubDate>Mon, 26 Oct 2026 10:00:00 +0000</pubDate><description>Cheap flights from MAN</description></item>
<item><title>Germany to Bali for only \xe2\x82\xac400</title><link>https://example.com/b</link><description>From Berlin</description></item>
<item><title>London, UK to Tokyo for \xc2\xa3350</title><link>https://example.com/c</link></item>
</channel></rss>"""


def test_parse_feed_matches_home_airport():
    deals = parse_feed(RSS, "example.com", keywords_for(["MAN"]))
    by_link = {d.link: d for d in deals}
    assert set(by_link["https://example.com/a"].matched) == {"Manchester", "MAN"}
    assert by_link["https://example.com/b"].matched == []  # "Germany" 不应命中 MAN
    assert by_link["https://example.com/c"].matched == ["UK"]
    assert extract_price("to Shanghai for £299 return") == "£299"
    assert match("from MANCHESTER", ["Manchester"]) == ["Manchester"]
