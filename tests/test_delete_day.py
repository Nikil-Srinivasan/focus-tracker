"""Delete all data for one chosen tracker day from the dashboard."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from conftest import FakeCapturer, FakeSystem
from focus_tracker import plan
from focus_tracker.agent import Agent
from focus_tracker.categorize import load_rules
from focus_tracker.dashboard import ApiError, Dashboard
from focus_tracker.metrics import Classifier

# Tracker days run 04:00 to 04:00 local time (day_rollover_hour = 4).
DAY_START = datetime(2026, 11, 23, 4, 0, 0).timestamp()  # Mon 23 Nov
DAY_END = datetime(2026, 11, 24, 4, 0, 0).timestamp()
NOW = datetime(2026, 11, 25, 11, 0, 0).timestamp()  # Wed 25 Nov, tracker running
MIN = 60


def add(store, start, end, title="agent.py - Visual Studio Code"):
    store._exec(
        "INSERT INTO activity(start_ts, end_ts, process, title, monitor) VALUES(?, ?, 'Code.exe', ?, 1)",
        (start, end, title),
    )
    store._exec("INSERT INTO state(start_ts, end_ts, state) VALUES(?, ?, 'active')", (start, end))


def shot(cfg, store, ts, deleted=0):
    dt = datetime.fromtimestamp(ts)
    rel = f"{dt:%Y-%m-%d}/{dt:%H}/{dt:%H%M%S}_m1.jpg"
    (cfg.shots_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (cfg.shots_dir / rel).write_bytes(b"x")
    store._exec("INSERT INTO screenshot(ts, monitor, path, bytes, deleted) VALUES(?, 1, ?, 1, ?)", (ts, rel, deleted))
    return cfg.shots_dir / rel


@pytest.fixture
def cap():
    return FakeCapturer()


@pytest.fixture
def agent(cfg, store, cap):
    return Agent(cfg, store, FakeSystem(), lambda: cap, None, None, None)


@pytest.fixture
def dash(cfg, store, agent, tmp_path):
    return Dashboard(cfg, store, agent, rules_path=tmp_path / "rules.toml", clock=lambda: NOW)


def delete(dash, day):
    return dash.post("/api/delete", {"range": "day", "day": day})


def test_deletes_everything_in_the_day_and_trims_edges(dash, cfg, store):
    add(store, DAY_START - 30 * MIN, DAY_START + 30 * MIN)  # straddles the start
    add(store, DAY_START + 6 * 3600, DAY_START + 7 * 3600)  # inside
    add(store, DAY_END - 10 * MIN, DAY_END + 20 * MIN)  # straddles the end (after midnight)
    store.add_break_label(DAY_START + 8 * 3600, DAY_START + 8.5 * 3600, "Lunch")
    store.set_goal("2026-11-23", DAY_START + 60, "Finish DP chapter", "dp")
    store.set_goal("2026-11-22", DAY_START - 86400, "Other day", None)
    inside = [shot(cfg, store, DAY_START + 6 * 3600 + 5), shot(cfg, store, DAY_END - 60)]  # 02:59 next date
    wiped = shot(cfg, store, DAY_START + 6 * 3600 + 10, deleted=1)
    before = shot(cfg, store, DAY_START - 60)
    after = shot(cfg, store, DAY_END + 60)
    orphan = cfg.shots_dir / "2026-11-23" / "15" / "150000_m2.jpg"  # file the database never saw
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"x")

    summary = dash.get("/api/day_summary", {"day": "2026-11-23"})
    assert summary["screenshots"] == 2 and summary["goal"] and not summary["empty"]
    assert summary["activity_min"] == pytest.approx(30 + 60 + 10)

    r = delete(dash, "2026-11-23")
    assert r["ok"] and r["screenshots_removed"] == 4  # 2 live + already-wiped row's file + orphan

    assert store.query("SELECT start_ts, end_ts FROM activity ORDER BY start_ts") == [
        (DAY_START - 30 * MIN, DAY_START), (DAY_END, DAY_END + 20 * MIN)]
    assert store.query("SELECT start_ts, end_ts FROM state ORDER BY start_ts") == [
        (DAY_START - 30 * MIN, DAY_START), (DAY_END, DAY_END + 20 * MIN)]
    assert store.query("SELECT COUNT(*) FROM break_label")[0][0] == 0
    assert store.get_goal("2026-11-23") is None and store.get_goal("2026-11-22") is not None
    assert store.query("SELECT COUNT(*) FROM screenshot WHERE ts >= ? AND ts < ?", (DAY_START, DAY_END))[0][0] == 0
    assert not any(p.exists() for p in inside + [wiped, orphan])
    assert before.exists() and after.exists()
    assert not (cfg.shots_dir / "2026-11-23" / "10").exists()  # empty hour folders removed
    assert (cfg.shots_dir / "2026-11-23" / "03").is_dir()  # previous tracker day's hours stay
    assert dash.get("/api/day_summary", {"day": "2026-11-23"})["empty"]


def test_day_with_no_data_is_a_no_op(dash, store):
    summary = dash.get("/api/day_summary", {"day": "2026-11-20"})
    assert summary["empty"] and summary["screenshots"] == 0
    assert delete(dash, "2026-11-20") == {"ok": True, "day": "2026-11-20", "screenshots_removed": 0,
                                          "plans_rebuilt": []}


def test_rejects_future_missing_and_bad_days(dash):
    for body in ({"range": "day"}, {"range": "day", "day": ""}, {"range": "day", "day": "23-11-2026"},
                 {"range": "day", "day": 20261123}, {"range": "day", "day": "2026-11-26"}):
        with pytest.raises(ApiError) as err:
            dash.post("/api/delete", body)
        assert err.value.status == 400


def test_today_while_tracker_runs_keeps_recording(dash, agent, cap, cfg, store):
    today_start = datetime(2026, 11, 25, 4, 0, 0).timestamp()
    for i in range(0, 61):
        cap.shade = {1: (i * 7) % 250, 2: (i * 3) % 250}
        agent.tick(NOW - 60 + i)
    store.set_goal("2026-11-25", today_start + 60, "Goal", None)
    assert store.query("SELECT COUNT(*) FROM activity")[0][0] == 1
    r = delete(dash, "2026-11-25")
    assert r["screenshots_removed"] >= 1
    assert store.query("SELECT COUNT(*) FROM activity")[0][0] == 0
    assert store.get_goal("2026-11-25") is None
    assert not list(cfg.shots_dir.rglob("*.jpg"))
    agent.tick(NOW + 1)  # opens a fresh row; nothing stretches back over the deleted day
    assert store.query("SELECT start_ts, end_ts FROM activity") == [(NOW + 1, NOW + 1)]


def test_saved_plan_for_that_week_is_rebuilt_and_review_cleared(dash, store):
    classifier = Classifier(load_rules())
    for d in range(7):  # Mon 16 to Sun 22 Nov, 2 h of coding each day at 10:00
        start = datetime(2026, 11, 16 + d, 10, 0, 0).timestamp()
        add(store, start, start + 2 * 3600)
    week = plan.build_plan(store, classifier, datetime(2026, 11, 22).date(), 4, NOW)
    store.save_plan("2026-11-22", NOW, json.dumps(week))
    store.set_plan_review("2026-11-22", "Old write-up")
    store.save_plan("2026-11-08", NOW, json.dumps({**week, "end_day": "2026-11-08", "tracked_days": 99}))
    assert week["tracked_days"] == 7

    assert dash.get("/api/day_summary", {"day": "2026-11-18"})["plans"] == ["2026-11-22"]
    r = delete(dash, "2026-11-18")
    assert r["plans_rebuilt"] == ["2026-11-22"]
    end_day, data, review = store.get_plan("2026-11-22")
    assert json.loads(data)["tracked_days"] == 6 and review is None
    assert json.loads(store.get_plan("2026-11-08")[1])["tracked_days"] == 99  # other weeks untouched


def test_refuses_while_ai_review_runs_for_that_week(dash, store):
    store.save_plan("2026-11-22", NOW, json.dumps({"end_day": "2026-11-22"}))
    dash._reviews["2026-11-22"] = "running"
    with pytest.raises(ApiError) as err:
        delete(dash, "2026-11-20")
    assert err.value.status == 409
    assert delete(dash, "2026-11-10")["ok"]  # a different week is fine
