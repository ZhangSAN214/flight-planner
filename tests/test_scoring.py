from datetime import datetime, timedelta

from flightplanner.models import Itinerary, Segment, Slice
from flightplanner.scoring import Mode, Preferences, dedupe, generalized_cost, pareto_front, rank


def itin(id, price, minutes, stops=0, hour=10, source="A", flight="XX 1", gap=120):
    t = datetime(2026, 12, 1, hour)
    segs = []
    per = minutes // (stops + 1)
    for i in range(stops + 1):
        segs.append(Segment(flight_number=f"{flight}{i}", carrier_name="X", origin="AAA", destination="BBB", depart=t, arrive=t + timedelta(minutes=per), duration_minutes=per))
        t += timedelta(minutes=per + gap)
    return Itinerary(id=id, source=source, price=price, currency="GBP", slices=[Slice(segments=segs, duration_minutes=minutes)])


def test_generalized_cost_penalties():
    prefs = Preferences(hour_value=10, stop_penalty=20, redeye_penalty=30, tight_connection_penalty=50, min_connection_minutes=60)
    assert generalized_cost(itin("a", 100, 120), prefs) == 120
    assert generalized_cost(itin("b", 100, 120, stops=1), prefs) == 140
    assert generalized_cost(itin("c", 100, 120, hour=2), prefs) == 150
    assert generalized_cost(itin("d", 100, 120, stops=1, gap=40), prefs) == 190


def test_pareto_front():
    its = [itin("cheap", 100, 600), itin("fast", 300, 120), itin("mid", 200, 300), itin("dominated", 250, 400)]
    assert pareto_front(its) == {"cheap", "fast", "mid"}


def test_rank_modes_and_tags():
    its = [itin("cheap", 100, 900), itin("fast", 400, 120), itin("balanced", 180, 300)]
    ranked = rank(its, Preferences(mode=Mode.best, hour_value=20))
    assert ranked[0].id == "balanced"
    tags = {i.id: i.tags for i in ranked}
    assert "cheapest" in tags["cheap"] and "fastest" in tags["fast"] and "best" in tags["balanced"]
    assert [i.id for i in rank(its, Preferences(mode=Mode.cheapest))][0] == "cheap"
    assert [i.id for i in rank(its, Preferences(mode=Mode.fastest))][0] == "fast"
    # 时间不值钱时，综合最优就是最便宜
    assert rank(its, Preferences(mode=Mode.best, hour_value=0))[0].id == "cheap"


def test_dedupe_keeps_cheapest_and_records_source():
    a = itin("a", 500, 600, source="Google Flights", flight="EK 1")
    b = itin("b", 480, 600, source="Duffel", flight="EK 1")
    out = dedupe([a, b])
    assert len(out) == 1 and out[0].id == "b" and "also:Google Flights" in out[0].tags
