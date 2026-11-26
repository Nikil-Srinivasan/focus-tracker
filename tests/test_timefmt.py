"""Everything shown to the user uses the 12-hour clock with AM/PM."""

from __future__ import annotations

from datetime import date, datetime

from conftest import FakeCapturer, FakeSystem
from focus_tracker import metrics, plan
from focus_tracker.agent import Agent
from focus_tracker.categorize import load_rules
from focus_tracker.dashboard import Dashboard
from focus_tracker.timefmt import clock12, hhmm12, hour12


def test_hour12_edges():
    assert [hour12(h) for h in (0, 1, 11, 12, 13, 23)] == ["12 AM", "1 AM", "11 AM", "12 PM", "1 PM", "11 PM"]
    assert hour12(24) == "12 AM" and hour12(28) == "4 AM"  # rollover + offset wraps


def test_clock12_and_config_times():
    assert clock12(datetime(2026, 1, 1, 0, 5)) == "12:05 AM"
    assert clock12(datetime(2026, 1, 1, 9, 5)) == "9:05 AM"
    assert clock12(datetime(2026, 1, 1, 12, 0)) == "12:00 PM"
    assert clock12(datetime(2026, 1, 1, 23, 59)) == "11:59 PM"
    assert hhmm12("23:59") == "11:59 PM" and hhmm12("00:00") == "12:00 AM"


def test_settings_show_12_hour(cfg, store, tmp_path):
    agent = Agent(cfg, store, FakeSystem(), lambda: FakeCapturer(), None, None, None)
    s = Dashboard(cfg, store, agent, rules_path=tmp_path / "rules.toml").settings()
    assert s["wipe"] == "Sun 11:59 PM"
    assert s["day_starts"] == "4 AM"


def test_best_hours_12_hour_and_wraps_past_midnight(store):
    def deep(day, hour, minutes):
        start = datetime(2026, 11, day, hour, 0).timestamp()
        store._exec("INSERT INTO activity(start_ts, end_ts, process, title, monitor, url) VALUES(?, ?, ?, ?, 1, NULL)",
                    (start, start + minutes * 60, "Code.exe", "agent.py - focus-tracker - Visual Studio Code"))
        store._exec("INSERT INTO state(start_ts, end_ts, state) VALUES(?, ?, 'active')", (start, start + minutes * 60))

    deep(24, 22, 120)  # 10 PM to midnight
    assert plan.best_hours(store, metrics.Classifier(load_rules()), date(2026, 11, 24), 4) == "10 PM to 12 AM"
