"""Weekly wipe of all screenshots, plus a hard disk cap."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .db import Store

log = logging.getLogger(__name__)

LAST_WIPE_KEY = "last_wipe_ts"


def last_wipe_boundary(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    """Most recent scheduled wipe time at or before `now` (naive local time)."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate -= timedelta(days=(candidate.weekday() - weekday) % 7)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


def maybe_wipe(store: Store, cfg: Config, now_ts: float) -> int:
    """Delete every screenshot taken before the latest scheduled wipe time, once.

    If the PC was off or asleep at the scheduled time, this runs on the next tick
    after it comes back. Returns the number of files deleted.
    """
    hour, minute = cfg.wipe_hour_minute
    boundary = last_wipe_boundary(datetime.fromtimestamp(now_ts), cfg.wipe_weekday, hour, minute)
    boundary_ts = boundary.timestamp()
    last = store.get_meta(LAST_WIPE_KEY)
    if last is None:
        # First run: nothing older exists yet; start the schedule from here.
        store.set_meta(LAST_WIPE_KEY, str(now_ts))
        return 0
    if float(last) >= boundary_ts:
        return 0
    deleted = wipe_before(store, cfg.shots_dir, boundary_ts)
    store.set_meta(LAST_WIPE_KEY, str(now_ts))
    log.info("Weekly wipe: deleted %d screenshots taken before %s", deleted, boundary)
    return deleted


def wipe_before(store: Store, shots_dir: Path, boundary_ts: float) -> int:
    rows = store.live_screenshots_before(boundary_ts)
    ids = []
    for shot_id, rel in rows:
        try:
            (shots_dir / rel).unlink(missing_ok=True)
        except OSError:
            log.warning("Could not delete %s", rel, exc_info=True)
            continue
        ids.append(shot_id)
    store.mark_deleted(ids)
    # Catch files the database does not know about (e.g. after a crash).
    _sweep_files_older_than(shots_dir, boundary_ts)
    remove_empty_dirs(shots_dir)
    return len(ids)


def enforce_cap(store: Store, cfg: Config) -> int:
    """If live screenshots exceed max_storage_gb, delete whole days, oldest first,
    down to 90% of the cap. Never deletes today's folder. Returns days deleted."""
    cap = cfg.max_storage_gb * 1024**3
    total = store.live_screenshot_bytes()
    if total <= cap:
        return 0
    days = store.live_screenshot_days()
    deleted = 0
    for day, size in days[:-1]:  # keep the newest day
        if total <= cap * 0.9:
            break
        _remove_tree(cfg.shots_dir / day)
        store.mark_day_deleted(day)
        total -= size
        deleted += 1
        log.warning("Storage cap: deleted screenshots for %s", day)
    return deleted


def _sweep_files_older_than(shots_dir: Path, ts: float) -> None:
    """Delete stray screenshot files whose name says they were taken before `ts`."""
    if not shots_dir.exists():
        return
    for path in shots_dir.rglob("*.jpg"):
        taken = shot_time_from_path(path.relative_to(shots_dir))
        if taken is not None and taken < ts:
            try:
                path.unlink()
            except OSError:
                pass


def shot_time_from_path(rel: Path) -> float | None:
    """'2026-11-24/10/105634_m1.jpg' -> timestamp, or None if it does not match."""
    try:
        day = rel.parts[0]
        hms = rel.name.split("_", 1)[0]
        return datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H%M%S").timestamp()
    except (IndexError, ValueError):
        return None


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for p in sorted(path.rglob("*"), reverse=True):
        try:
            p.unlink() if p.is_file() else p.rmdir()
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        if Path(dirpath) != root and not os.listdir(dirpath):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
