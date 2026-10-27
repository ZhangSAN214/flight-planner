"""排序：把每个方案换算成「广义成本」，再按目标排序，并找出帕累托前沿。

广义成本 = 价格 + 时间价值 × 总小时 + 中转惩罚 × 中转次数 + 红眼航班惩罚 + 衔接过短惩罚
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import Itinerary


class Mode(str, Enum):
    best = "best"          # 综合最优
    cheapest = "cheapest"  # 最便宜
    fastest = "fastest"    # 最快
    fewest_stops = "fewest_stops"


@dataclass
class Preferences:
    mode: Mode = Mode.best
    hour_value: float = 15.0          # 多飞 1 小时相当于多花多少钱
    stop_penalty: float = 25.0        # 每多一次中转相当于多花多少钱
    redeye_penalty: float = 20.0      # 0:00–5:59 起飞
    min_connection_minutes: int = 60  # 短于此视为衔接过紧
    tight_connection_penalty: float = 60.0


def connection_minutes(itin: Itinerary) -> list[int]:
    gaps = []
    for s in itin.slices:
        for a, b in zip(s.segments, s.segments[1:]):
            gaps.append(int((b.depart - a.arrive).total_seconds() // 60))
    return gaps


def is_redeye(itin: Itinerary) -> bool:
    return any(s.segments and s.segments[0].depart.hour < 6 for s in itin.slices)


def generalized_cost(itin: Itinerary, prefs: Preferences) -> float:
    cost = itin.comparable_price()
    cost += prefs.hour_value * itin.total_minutes / 60
    cost += prefs.stop_penalty * itin.total_stops
    if is_redeye(itin):
        cost += prefs.redeye_penalty
    cost += prefs.tight_connection_penalty * sum(1 for g in connection_minutes(itin) if g < prefs.min_connection_minutes)
    return round(cost, 2)


def pareto_front(itins: list[Itinerary]) -> set[str]:
    """没有任何其他方案同时更便宜且更快的方案。"""
    front: set[str] = set()
    for a in itins:
        dominated = any(
            b.comparable_price() <= a.comparable_price()
            and b.total_minutes <= a.total_minutes
            and (b.comparable_price() < a.comparable_price() or b.total_minutes < a.total_minutes)
            for b in itins
        )
        if not dominated:
            front.add(a.id)
    return front


def dedupe(itins: list[Itinerary]) -> list[Itinerary]:
    """多个数据源返回同一组航班时只保留最便宜的，并记下其他来源。"""
    best: dict[str, Itinerary] = {}
    for it in itins:
        key = it.fingerprint() or it.id
        cur = best.get(key)
        if cur is None:
            best[key] = it
            continue
        cheaper, other = (it, cur) if it.comparable_price() < cur.comparable_price() else (cur, it)
        tag = f"also:{other.source}"
        if tag not in cheaper.tags and other.source != cheaper.source:
            cheaper.tags.append(tag)
        best[key] = cheaper
    return list(best.values())


def rank(itins: list[Itinerary], prefs: Preferences) -> list[Itinerary]:
    if not itins:
        return []
    for it in itins:
        it.score = generalized_cost(it, prefs)
        it.tags = [t for t in it.tags if t not in {"cheapest", "fastest", "best", "pareto", "redeye", "tight_connection"}]
        if is_redeye(it):
            it.tags.append("redeye")
        if any(g < prefs.min_connection_minutes for g in connection_minutes(it)):
            it.tags.append("tight_connection")

    min(itins, key=lambda i: i.comparable_price()).tags.append("cheapest")
    min(itins, key=lambda i: (i.total_minutes, i.comparable_price())).tags.append("fastest")
    min(itins, key=lambda i: i.score).tags.append("best")
    front = pareto_front(itins)
    for it in itins:
        if it.id in front:
            it.tags.append("pareto")

    keys = {
        Mode.best: lambda i: (i.score, i.comparable_price()),
        Mode.cheapest: lambda i: (i.comparable_price(), i.total_minutes),
        Mode.fastest: lambda i: (i.total_minutes, i.comparable_price()),
        Mode.fewest_stops: lambda i: (i.total_stops, i.comparable_price()),
    }
    return sorted(itins, key=keys[prefs.mode])
