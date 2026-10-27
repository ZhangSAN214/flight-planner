"""航线监控：定时查价、记录历史、发现特价时提醒。

提醒条件（满足任一）：
1. 最低价 ≤ 你设定的目标价；
2. 已有至少 3 次历史记录，且最低价比历史中位数低 30% 以上。
同一出发日、同一或更高价格 72 小时内不重复提醒。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from ..db import Store, utcnow
from ..models import Watch
from ..providers import ProviderError
from ..service import Planner
from .feeds import fetch_deals
from .notify import notify

log = logging.getLogger(__name__)

DROP_RATIO = 0.7
MIN_HISTORY = 3


def should_alert(price: float, target: float | None, median: float | None, history_count: int) -> str | None:
    if target is not None and price <= target:
        return f"低于目标价 {target:g}"
    if median and history_count >= MIN_HISTORY and price <= median * DROP_RATIO:
        return f"比历史中位数 {median:g} 低 {round((1 - price / median) * 100)}%"
    return None


async def check_watch(planner: Planner, store: Store, watch: Watch, send: bool = True) -> dict:
    start = max(watch.depart_from, date.today())
    if watch.depart_to < start:
        return {"watch_id": watch.id, "status": "expired", "message": "出发日期已过"}
    checked_at = utcnow()
    median_before = store.history_median(watch.id, exclude_after=checked_at)
    history_count = len(store.history_daily_min(watch.id, exclude_after=checked_at))
    try:
        result = await planner.flexible(watch.origin, watch.destination, start, watch.depart_to, watch.trip_days)
    except ProviderError as e:
        return {"watch_id": watch.id, "status": "error", "message": str(e)}
    quotes = result["quotes"]
    if not quotes:
        store.watch_mark_checked(watch.id, None, None)
        return {"watch_id": watch.id, "status": "no_data", "message": "; ".join(result["errors"]) or "没有查到价格"}
    for q in quotes:
        store.history_add(watch.id, date.fromisoformat(q["depart_date"]), q["price"], q["currency"], q["source"], checked_at)
    best = min(quotes, key=lambda q: q["price"])
    best_date = date.fromisoformat(best["depart_date"])
    store.watch_mark_checked(watch.id, best["price"], best_date)

    reason = should_alert(best["price"], watch.target_price, median_before, history_count)
    sent: list[str] = []
    if reason and send and not store.alert_already_sent(watch.id, best_date, best["price"]):
        trip = f"，停留 {watch.trip_days} 天" if watch.trip_days else "，单程"
        subject = f"特价提醒：{watch.origin}→{watch.destination} {best['currency']} {best['price']:g}"
        body = (
            f"{watch.origin} → {watch.destination}{trip}\n"
            f"最低价：{best['currency']} {best['price']:g}（{best_date} 出发）\n"
            f"原因：{reason}\n"
            f"来源：{best['source']}（缓存价请以实时查询为准）"
        )
        sent = await notify(planner.settings, subject, body)
        store.alert_record(watch.id, best_date, best["price"])
    return {
        "watch_id": watch.id,
        "status": "ok",
        "best_price": best["price"],
        "best_date": best["depart_date"],
        "median_before": median_before,
        "alert": reason,
        "sent": sent,
    }


async def check_all(planner: Planner, store: Store) -> list[dict]:
    results = []
    for w in store.watch_list(active_only=True):
        results.append(await check_watch(planner, store, w))
    return results


async def check_deal_feeds(planner: Planner, store: Store) -> int:
    """把命中出发地的新特价帖推送给你，返回新推送数量。"""
    settings = planner.settings
    if not settings.deal_feeds:
        return 0
    deals, _ = await fetch_deals(settings.deal_feeds, [settings.home_airport])
    new = [d for d in deals if d.matched and d.link and store.cache_get(f"dealseen:{d.link}") is None]
    for d in new:
        store.cache_set(f"dealseen:{d.link}", True, minutes=60 * 24 * 60)
    if new:
        body = "\n\n".join(f"{d.title}\n{d.link}" for d in new[:10])
        await notify(settings, f"特价网站有 {len(new)} 条和 {settings.home_airport} 相关的新帖", body)
    return len(new)


async def run_scheduler(planner: Planner, store: Store) -> None:
    interval = max(planner.settings.watch_interval_hours, 0.5) * 3600
    while True:
        try:
            await check_all(planner, store)
            await check_deal_feeds(planner, store)
        except Exception:  # noqa: BLE001 定时任务不能因为一次出错就停掉
            log.exception("定时检查出错")
        await asyncio.sleep(interval)
