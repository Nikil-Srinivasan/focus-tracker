from __future__ import annotations

from datetime import datetime

from focus_tracker import retention
from focus_tracker.retention import LAST_WIPE_KEY, last_wipe_boundary


def test_boundary_is_last_sunday_2359():
    tue = datetime(2026, 11, 24, 10, 0)
    assert last_wipe_boundary(tue, 6, 23, 59) == datetime(2026, 11, 22, 23, 59)
    sun_before = datetime(2026, 11, 29, 23, 0)
    assert last_wipe_boundary(sun_before, 6, 23, 59) == datetime(2026, 11, 22, 23, 59)
    sun_after = datetime(2026, 11, 29, 23, 59, 30)
    assert last_wipe_boundary(sun_after, 6, 23, 59) == datetime(2026, 11, 29, 23, 59)


def _add_shot(cfg, store, dt, monitor=1, size=1000):
    rel = f"{dt:%Y-%m-%d}/{dt:%H}/{dt:%H%M%S}_m{monitor}.jpg"
    path = cfg.shots_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    store.add_screenshot(dt.timestamp(), monitor, rel, size, None)
    return path


def test_first_run_only_records_schedule(cfg, store):
    now = datetime(2026, 11, 24, 10).timestamp()
    assert retention.maybe_wipe(store, cfg, now) == 0
    assert float(store.get_meta(LAST_WIPE_KEY)) == now


def test_weekly_wipe_deletes_everything_before_sunday(cfg, store):
    store.set_meta(LAST_WIPE_KEY, str(datetime(2026, 11, 23, 9).timestamp()))
    old = _add_shot(cfg, store, datetime(2026, 11, 25, 10))
    older = _add_shot(cfg, store, datetime(2026, 11, 29, 23, 58), monitor=2)
    # Monday morning after the Sunday 23:59 boundary; PC was off at 23:59.
    now = datetime(2026, 11, 30, 9).timestamp()
    newer = _add_shot(cfg, store, datetime(2026, 11, 30, 8, 59))
    assert retention.maybe_wipe(store, cfg, now) == 2
    assert not old.exists() and not older.exists() and newer.exists()
    assert not (cfg.shots_dir / "2026-11-25").exists()
    # Runs once per boundary.
    assert retention.maybe_wipe(store, cfg, now + 60) == 0
    assert store.query("SELECT COUNT(*) FROM screenshot WHERE deleted = 0")[0][0] == 1


def test_cap_deletes_oldest_days_but_keeps_today(cfg, store):
    cfg.max_storage_gb = 3000 / 1024**3  # 3000 bytes
    d1 = _add_shot(cfg, store, datetime(2026, 11, 24, 10), size=2000)
    d2 = _add_shot(cfg, store, datetime(2026, 11, 25, 10), size=2000)
    d3 = _add_shot(cfg, store, datetime(2026, 11, 26, 10), size=2000)
    assert retention.enforce_cap(store, cfg) == 2
    assert not d1.exists() and not d2.exists() and d3.exists()
