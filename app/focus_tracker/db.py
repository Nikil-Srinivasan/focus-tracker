"""SQLite store for activity spans, state spans, screenshots, goals and metadata."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- One row per continuous stretch in the same app + window title + monitor.
CREATE TABLE IF NOT EXISTS activity (
    id       INTEGER PRIMARY KEY,
    start_ts REAL NOT NULL,
    end_ts   REAL NOT NULL,
    process  TEXT NOT NULL,
    title    TEXT NOT NULL,
    monitor  INTEGER
);
CREATE INDEX IF NOT EXISTS activity_start ON activity(start_ts);
-- One row per continuous stretch of active / idle / locked / paused.
CREATE TABLE IF NOT EXISTS state (
    id       INTEGER PRIMARY KEY,
    start_ts REAL NOT NULL,
    end_ts   REAL NOT NULL,
    state    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS state_start ON state(start_ts);
CREATE TABLE IF NOT EXISTS screenshot (
    id          INTEGER PRIMARY KEY,
    ts          REAL NOT NULL,
    monitor     INTEGER NOT NULL,
    path        TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    activity_id INTEGER REFERENCES activity(id),
    deleted     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS screenshot_ts ON screenshot(ts);
-- Daily goal; text NULL means the prompt was skipped that day.
CREATE TABLE IF NOT EXISTS goal (
    day      TEXT PRIMARY KEY,
    set_ts   REAL NOT NULL,
    text     TEXT,
    keywords TEXT
);
"""


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # meta
    def get_meta(self, key: str) -> str | None:
        rows = self.query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # activity
    def open_activity(self, ts: float, process: str, title: str, monitor: int | None) -> int:
        cur = self._exec(
            "INSERT INTO activity(start_ts, end_ts, process, title, monitor) VALUES(?, ?, ?, ?, ?)",
            (ts, ts, process, title, monitor),
        )
        return int(cur.lastrowid)

    def extend_activity(self, activity_id: int, ts: float) -> None:
        self._exec("UPDATE activity SET end_ts = ? WHERE id = ?", (ts, activity_id))

    # state
    def open_state(self, ts: float, state: str) -> int:
        cur = self._exec(
            "INSERT INTO state(start_ts, end_ts, state) VALUES(?, ?, ?)", (ts, ts, state)
        )
        return int(cur.lastrowid)

    def extend_state(self, state_id: int, ts: float) -> None:
        self._exec("UPDATE state SET end_ts = ? WHERE id = ?", (ts, state_id))

    # screenshots
    def add_screenshot(
        self, ts: float, monitor: int, path: str, size: int, activity_id: int | None
    ) -> int:
        cur = self._exec(
            "INSERT INTO screenshot(ts, monitor, path, bytes, activity_id) VALUES(?, ?, ?, ?, ?)",
            (ts, monitor, path, size, activity_id),
        )
        return int(cur.lastrowid)

    def live_screenshots_before(self, ts: float) -> list[tuple[int, str]]:
        return self.query(
            "SELECT id, path FROM screenshot WHERE deleted = 0 AND ts < ? ORDER BY ts", (ts,)
        )

    def live_screenshot_days(self) -> list[tuple[str, int]]:
        """(day folder, bytes) for live screenshots, oldest first."""
        rows = self.query("SELECT path, bytes FROM screenshot WHERE deleted = 0 ORDER BY ts")
        days: dict[str, int] = {}
        for path, size in rows:
            day = Path(path).parts[0]
            days[day] = days.get(day, 0) + size
        return list(days.items())

    def live_screenshot_bytes(self) -> int:
        return int(self.query("SELECT COALESCE(SUM(bytes), 0) FROM screenshot WHERE deleted = 0")[0][0])

    def mark_deleted(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany("UPDATE screenshot SET deleted = 1 WHERE id = ?", [(i,) for i in ids])

    def mark_day_deleted(self, day: str) -> None:
        self._exec("UPDATE screenshot SET deleted = 1 WHERE path LIKE ?", (f"{day}/%",))

    # goals
    def get_goal(self, day: str) -> tuple[str | None, str | None] | None:
        rows = self.query("SELECT text, keywords FROM goal WHERE day = ?", (day,))
        return rows[0] if rows else None

    def set_goal(self, day: str, ts: float, text: str | None, keywords: str | None) -> None:
        self._exec(
            "INSERT INTO goal(day, set_ts, text, keywords) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET set_ts = excluded.set_ts, text = excluded.text, "
            "keywords = excluded.keywords",
            (day, ts, text, keywords),
        )
