"""SQLite persistence: full price history per item + alert bookkeeping."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key    TEXT NOT NULL,
    price       REAL NOT NULL,
    method      TEXT NOT NULL,
    checked_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_item ON price_history (item_key, checked_at);

CREATE TABLE IF NOT EXISTS alert_state (
    item_key            TEXT PRIMARY KEY,
    last_alert_price    REAL,
    last_alert_at       TEXT
);

CREATE TABLE IF NOT EXISTS watch_state (
    item_key    TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class HistoryRow:
    price: float
    method: str
    checked_at: datetime


@dataclass(frozen=True)
class AlertState:
    last_alert_price: float | None
    last_alert_at: datetime | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Storage:
    """Thin wrapper over SQLite. Safe to use as a context manager."""

    def __init__(self, path: str = "lookout.db"):
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- price history ---------------------------------------------------

    def record_price(self, item_key: str, price: float, method: str,
                     at: datetime | None = None) -> None:
        at = at or _utcnow()
        self._conn.execute(
            "INSERT INTO price_history (item_key, price, method, checked_at) VALUES (?,?,?,?)",
            (item_key, price, method, at.isoformat()),
        )
        self._conn.commit()

    def history(self, item_key: str, limit: int = 50) -> list[HistoryRow]:
        rows = self._conn.execute(
            "SELECT price, method, checked_at FROM price_history "
            "WHERE item_key = ? ORDER BY checked_at DESC LIMIT ?",
            (item_key, limit),
        ).fetchall()
        return [
            HistoryRow(price=r[0], method=r[1], checked_at=datetime.fromisoformat(r[2]))
            for r in rows
        ]

    def lowest_price(self, item_key: str) -> float | None:
        row = self._conn.execute(
            "SELECT MIN(price) FROM price_history WHERE item_key = ?", (item_key,)
        ).fetchone()
        return row[0]

    # -- alert bookkeeping -------------------------------------------------

    def get_alert_state(self, item_key: str) -> AlertState:
        row = self._conn.execute(
            "SELECT last_alert_price, last_alert_at FROM alert_state WHERE item_key = ?",
            (item_key,),
        ).fetchone()
        if row is None:
            return AlertState(None, None)
        return AlertState(
            last_alert_price=row[0],
            last_alert_at=datetime.fromisoformat(row[1]) if row[1] else None,
        )

    def record_alert(self, item_key: str, price: float, at: datetime | None = None) -> None:
        at = at or _utcnow()
        self._conn.execute(
            "INSERT INTO alert_state (item_key, last_alert_price, last_alert_at) "
            "VALUES (?,?,?) ON CONFLICT(item_key) DO UPDATE SET "
            "last_alert_price = excluded.last_alert_price, "
            "last_alert_at = excluded.last_alert_at",
            (item_key, price, at.isoformat()),
        )
        self._conn.commit()

    def clear_alert(self, item_key: str) -> None:
        """Reset alert state (called when the price climbs back above target)."""
        self._conn.execute("DELETE FROM alert_state WHERE item_key = ?", (item_key,))
        self._conn.commit()

    # -- generic watch state (stock / keyword / change monitors) -----------

    def get_watch_state(self, item_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM watch_state WHERE item_key = ?", (item_key,)
        ).fetchone()
        return row[0] if row else None

    def set_watch_state(self, item_key: str, state: str,
                        at: datetime | None = None) -> None:
        at = at or _utcnow()
        self._conn.execute(
            "INSERT INTO watch_state (item_key, state, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(item_key) DO UPDATE SET state = excluded.state, "
            "updated_at = excluded.updated_at",
            (item_key, state, at.isoformat()),
        )
        self._conn.commit()

    # -- CSV export ----------------------------------------------------------

    def all_history(self, item_key: str | None = None) -> list[tuple[str, float, str, str]]:
        """(item_key, price, method, checked_at) rows, oldest first."""
        if item_key:
            rows = self._conn.execute(
                "SELECT item_key, price, method, checked_at FROM price_history "
                "WHERE item_key = ? ORDER BY checked_at", (item_key,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT item_key, price, method, checked_at FROM price_history "
                "ORDER BY item_key, checked_at").fetchall()
        return rows

    def stats(self, item_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT COUNT(*), MIN(price), MAX(price), AVG(price) "
            "FROM price_history WHERE item_key = ?", (item_key,)).fetchone()
        if not row or row[0] == 0:
            return None
        return {"count": row[0], "min": row[1], "max": row[2], "avg": row[3]}

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
