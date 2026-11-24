from __future__ import annotations

from datetime import datetime

from conftest import FakeCapturer, FakeSystem

from focus_tracker.agent import EXCLUDED_TITLE, Agent, Span
from focus_tracker.system import Foreground

T0 = datetime(2026, 11, 24, 10, 0, 0).timestamp()  # a Tuesday


def make(cfg, store, prompter=None):
    system, cap = FakeSystem(), FakeCapturer()
    agent = Agent(cfg, store, system, lambda: cap, prompter)
    return agent, system, cap


def run(agent, start, seconds):
    for i in range(seconds):
        agent.tick(start + i)


def shots(store):
    return store.query("SELECT ts, monitor, path FROM screenshot ORDER BY id")


def test_screenshot_every_5s_both_monitors_with_dedupe(cfg, store):
    agent, _system, cap = make(cfg, store)
    run(agent, T0, 11)  # ticks at 0..10 -> captures at 0, 5, 10
    assert cap.grabs == 3
    # Frames never changed, so only the first capture per monitor is saved.
    assert [(m, p) for _, m, p in shots(store)] == [
        (1, "2026-11-24/10/100000_m1.jpg"),
        (2, "2026-11-24/10/100000_m2.jpg"),
    ]
    assert (cfg.shots_dir / "2026-11-24/10/100000_m1.jpg").exists()


def test_changed_monitor_is_saved(cfg, store):
    agent, _system, cap = make(cfg, store)
    agent.tick(T0)
    cap.shade[2] = 60
    agent.tick(T0 + 5)
    assert [m for _, m, _ in shots(store)] == [1, 2, 2]


def test_no_screenshots_when_locked_idle_or_paused(cfg, store):
    agent, system, cap = make(cfg, store)
    system.locked = True
    run(agent, T0, 10)
    system.locked = False
    system.idle = cfg.idle_threshold_s + 1
    run(agent, T0 + 10, 10)
    system.idle = 0
    agent.pause(60, now=T0 + 20)
    run(agent, T0 + 20, 10)
    assert cap.grabs == 0
    states = [s for (s,) in store.query("SELECT state FROM state ORDER BY id")]
    assert states == ["locked", "idle", "paused"]
    assert store.query("SELECT COUNT(*) FROM activity")[0][0] == 0


def test_activity_spans_merge_and_split(cfg, store):
    agent, system, _cap = make(cfg, store)
    run(agent, T0, 3)
    system.fg = Foreground("brave.exe", "YouTube - Brave", (1920, 0, 3840, 1080))
    run(agent, T0 + 3, 2)
    rows = store.query("SELECT start_ts, end_ts, process, title, monitor FROM activity ORDER BY id")
    assert rows == [
        (T0, T0 + 2, "Code.exe", "agent.py - focus-tracker", 1),
        (T0 + 3, T0 + 4, "brave.exe", "YouTube - Brave", 2),
    ]


def test_excluded_app_hides_title_and_skips_screenshot(cfg, store):
    agent, system, cap = make(cfg, store)
    system.fg = Foreground("Bitwarden.exe", "My vault", (0, 0, 1920, 1080))
    run(agent, T0, 6)
    assert cap.grabs == 0
    assert store.query("SELECT process, title FROM activity") == [("Bitwarden.exe", EXCLUDED_TITLE)]
    system.fg = Foreground("brave.exe", "Bank - InPrivate - Microsoft Edge", (0, 0, 1920, 1080))
    agent.tick(T0 + 10)
    assert cap.grabs == 0


def test_gap_after_sleep_opens_new_span(cfg, store):
    agent, _system, _cap = make(cfg, store)
    run(agent, T0, 3)
    run(agent, T0 + 3600, 3)  # laptop slept for an hour
    assert store.query("SELECT COUNT(*) FROM activity")[0][0] == 2
    assert store.query("SELECT COUNT(*) FROM state")[0][0] == 2


def test_goal_prompt_asked_once_per_day(cfg, store):
    asked = []

    def prompter(day):
        asked.append(day)
        return ("Ship phase 1", "focus-tracker, python")

    agent, _system, _cap = make(cfg, store, prompter)
    agent._ask_goal(agent.day_key(T0))  # run synchronously for the test
    run(agent, T0, 5)
    assert asked == ["2026-11-24"]
    assert store.get_goal("2026-11-24") == ("Ship phase 1", "focus-tracker, python")


def test_day_rollover_counts_late_night_as_previous_day(cfg, store):
    agent, _system, _cap = make(cfg, store)
    late = datetime(2026, 11, 25, 2, 30).timestamp()
    assert agent.day_key(late) == "2026-11-24"
    assert agent.next_day_start(late) == datetime(2026, 11, 25, 4, 0).timestamp()


def test_span_reopens_when_key_returns_after_change():
    opened = []
    span = Span(lambda ts, *k: opened.append((ts, k)) or len(opened), lambda i, ts: None, 10)
    span.update(0, ("a",))
    span.update(1, ("b",))
    span.update(2, ("a",))
    assert [k for _, k in opened] == [("a",), ("b",), ("a",)]


def test_no_screenshot_site_keeps_title(cfg, store):
    agent, system, cap = make(cfg, store)
    system.fg = Foreground("brave.exe", "Courses | Vanakkam DSA - Brave", (0, 0, 1920, 1080))
    run(agent, T0, 6)
    assert cap.grabs == 0
    assert store.query("SELECT title FROM activity") == [("Courses | Vanakkam DSA - Brave",)]


def test_bank_and_payment_titles_are_excluded(cfg, store):
    agent, system, cap = make(cfg, store)
    system.fg = Foreground("brave.exe", "ICICI Bank - Login - Brave", (0, 0, 1920, 1080))
    run(agent, T0, 6)
    assert cap.grabs == 0
    assert store.query("SELECT title FROM activity") == [(EXCLUDED_TITLE,)]
