"""SQLite 存储：查询缓存、API 用量、航线监控、价格历史。"""

from __future__ import annotations

import json
import sqlite3
import statistics
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Watch

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    provider TEXT NOT NULL,
    month TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, month)
);
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    depart_from TEXT NOT NULL,
    depart_to TEXT NOT NULL,
    trip_days INTEGER,
    target_price REAL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_checked TEXT,
    last_price REAL,
    last_price_date TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_watch ON price_history(watch_id, checked_at);
CREATE TABLE IF NOT EXISTS alerts_sent (
    watch_id INTEGER NOT NULL,
    depart_date TEXT NOT NULL,
    price REAL NOT NULL,
    sent_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # :memory: 数据库每个连接都是独立的，所以全程共用一个连接，用锁保护
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self.tx() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- 缓存 ----
    def cache_get(self, key: str) -> Any | None:
        with self.tx() as c:
            row = c.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]) < utcnow():
            return None
        return json.loads(row["value"])

    def cache_set(self, key: str, value: Any, minutes: int) -> None:
        expires = (utcnow() + timedelta(minutes=minutes)).isoformat()
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, default=str), expires),
            )

    # ---- API 用量 ----
    def usage_get(self, provider: str) -> int:
        month = utcnow().strftime("%Y-%m")
        with self.tx() as c:
            row = c.execute(
                "SELECT calls FROM api_usage WHERE provider = ? AND month = ?", (provider, month)
            ).fetchone()
        return row["calls"] if row else 0

    def usage_add(self, provider: str, n: int = 1) -> None:
        month = utcnow().strftime("%Y-%m")
        with self.tx() as c:
            c.execute(
                "INSERT INTO api_usage (provider, month, calls) VALUES (?, ?, ?) "
                "ON CONFLICT(provider, month) DO UPDATE SET calls = calls + excluded.calls",
                (provider, month, n),
            )

    # ---- 航线监控 ----
    def watch_add(self, w: Watch) -> Watch:
        now = utcnow()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO watches (origin, destination, depart_from, depart_to, trip_days, target_price, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (w.origin, w.destination, w.depart_from.isoformat(), w.depart_to.isoformat(), w.trip_days, w.target_price, now.isoformat()),
            )
            new_id = cur.lastrowid
        return w.model_copy(update={"id": new_id, "created_at": now, "active": True})

    def watch_list(self, active_only: bool = False) -> list[Watch]:
        sql = "SELECT * FROM watches" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        with self.tx() as c:
            rows = c.execute(sql).fetchall()
        return [self._row_to_watch(r) for r in rows]

    def watch_get(self, watch_id: int) -> Watch | None:
        with self.tx() as c:
            row = c.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
        return self._row_to_watch(row) if row else None

    def watch_delete(self, watch_id: int) -> bool:
        with self.tx() as c:
            cur = c.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        return cur.rowcount > 0

    def watch_mark_checked(self, watch_id: int, price: float | None, price_date: date | None) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE watches SET last_checked = ?, last_price = ?, last_price_date = ? WHERE id = ?",
                (utcnow().isoformat(), price, price_date.isoformat() if price_date else None, watch_id),
            )

    @staticmethod
    def _row_to_watch(r: sqlite3.Row) -> Watch:
        return Watch(
            id=r["id"],
            origin=r["origin"],
            destination=r["destination"],
            depart_from=date.fromisoformat(r["depart_from"]),
            depart_to=date.fromisoformat(r["depart_to"]),
            trip_days=r["trip_days"],
            target_price=r["target_price"],
            active=bool(r["active"]),
            created_at=datetime.fromisoformat(r["created_at"]),
            last_checked=datetime.fromisoformat(r["last_checked"]) if r["last_checked"] else None,
            last_price=r["last_price"],
            last_price_date=date.fromisoformat(r["last_price_date"]) if r["last_price_date"] else None,
        )

    # ---- 价格历史 ----
    def history_add(
        self, watch_id: int, depart_date: date, price: float, currency: str, source: str, checked_at: datetime | None = None
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO price_history (watch_id, checked_at, depart_date, price, currency, source) VALUES (?, ?, ?, ?, ?, ?)",
                (watch_id, (checked_at or utcnow()).isoformat(), depart_date.isoformat(), price, currency, source),
            )

    def history_list(self, watch_id: int) -> list[dict]:
        with self.tx() as c:
            rows = c.execute(
                "SELECT checked_at, depart_date, price, currency, source FROM price_history WHERE watch_id = ? ORDER BY checked_at",
                (watch_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def history_daily_min(self, watch_id: int, exclude_after: datetime | None = None) -> list[float]:
        """每次检查时的最低价，用来判断「比平时便宜多少」。"""
        sql = "SELECT checked_at, MIN(price) AS p FROM price_history WHERE watch_id = ?"
        params: list[Any] = [watch_id]
        if exclude_after is not None:
            sql += " AND checked_at < ?"
            params.append(exclude_after.isoformat())
        sql += " GROUP BY checked_at ORDER BY checked_at"
        with self.tx() as c:
            rows = c.execute(sql, params).fetchall()
        return [r["p"] for r in rows]

    def history_median(self, watch_id: int, exclude_after: datetime | None = None) -> float | None:
        prices = self.history_daily_min(watch_id, exclude_after)
        return statistics.median(prices) if prices else None

    # ---- 提醒去重 ----
    def alert_already_sent(self, watch_id: int, depart_date: date, price: float, within_hours: int = 72) -> bool:
        since = (utcnow() - timedelta(hours=within_hours)).isoformat()
        with self.tx() as c:
            row = c.execute(
                "SELECT 1 FROM alerts_sent WHERE watch_id = ? AND depart_date = ? AND price <= ? AND sent_at >= ?",
                (watch_id, depart_date.isoformat(), price, since),
            ).fetchone()
        return row is not None

    def alert_record(self, watch_id: int, depart_date: date, price: float) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO alerts_sent (watch_id, depart_date, price, sent_at) VALUES (?, ?, ?, ?)",
                (watch_id, depart_date.isoformat(), price, utcnow().isoformat()),
            )
