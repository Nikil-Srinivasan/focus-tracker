"""Nap answer, lock screen handling, neutral System category, first-in / last-out times."""

from __future__ import annotations

import threading
from datetime import date, datetime

from focus_tracker import metrics
from focus_tracker.categorize import categorize, goal_fit, load_rules
from focus_tracker.goal import BREAK_LABELS, break_labels
from focus_tracker.system import Foreground
from test_phase3 import (  # noqa: F401
    CODE,
    DAY,
    MIN,
    T0,
    YT_FUN,
    add,
    add_state,
    dash,
    make_agent,
    report,
)

LOCK = ("LockApp.exe", "Windows Default Lock Screen", "")


def join_break_threads():
    for t in threading.enumerate():
        if t.name == "break":
            t.join(2)


# Nap in the back-from-break prompt ------------------------------------------------

def test_nap_is_offered_only_after_a_long_enough_break():
    assert "Nap" not in break_labels(6 * MIN)
    assert break_labels(15 * MIN) == BREAK_LABELS and "Nap" in BREAK_LABELS


def test_nap_counts_as_rest_not_distraction(store):
    add(store, T0, 30, CODE)
    add_state(store, T0, T0 + 30 * MIN)
    for i, label in enumerate(("Distracted", "Nap", "Break", "Nap")):
        start = T0 + 3600 * (i + 1)
        store.add_break_label(start, start + 20 * MIN, label)
    today = report(store).today()
    assert [b["label"] for b in today["breaks"]] == ["Break", "Nap", "Distracted"]
    nap = next(b for b in today["breaks"] if b["label"] == "Nap")
    assert nap == {"label": "Nap", "count": 2, "minutes": 40, "rest": True}
    assert today["rest_min"] == 60
    assert today["kpis"]["distraction_min"] == 0


def test_nap_answer_is_saved(cfg, store):
    agent, system, _cap = make_agent(cfg, store, lambda away: "Nap")
    agent.tick(T0)
    system.locked = True
    agent.tick(T0 + 1)
    system.locked = False
    agent.tick(T0 + 1800)
    join_break_threads()
    assert store.query("SELECT label FROM break_label") == [("Nap",)]


# Lock screen ---------------------------------------------------------------------------

def test_lock_screen_in_front_counts_as_locked(cfg, store):
    """The lock screen curtain can be in front before Windows reports the session locked."""
    asked = []
    agent, system, _cap = make_agent(cfg, store, lambda away: asked.append(away) or "Break")
    agent.tick(T0)
    system.fg = Foreground(*LOCK[:2], (0, 0, 1920, 1080))
    system.switches = [(T0 + 0.5, system.fg)]
    for i in range(1, 700):
        agent.tick(T0 + i)
    assert agent.current_state == "locked"
    system.fg = Foreground(*CODE[:2], (0, 0, 1920, 1080))
    agent.tick(T0 + 700)
    join_break_threads()
    processes = {p for (p,) in store.query("SELECT process FROM activity")}
    assert processes == {"Code.exe"}
    assert store.query("SELECT COUNT(*) FROM screenshot WHERE ts > ? AND ts < ?", (T0 + 1, T0 + 700))[0][0] == 0
    assert len(asked) == 1  # a lock-screen stretch is a break like any other


def test_old_lock_screen_rows_are_ignored(store):
    add(store, T0, 14, LOCK)
    add(store, T0 + 14 * MIN, 10, CODE)
    r = report(store)
    assert [s.process for s in r.segs] == ["Code.exe"]
    q = metrics.uncategorized(store, metrics.Classifier(load_rules()), T0 - 1, T0 + 3600)
    assert q == []


# Categories from the "Not sure" list ---------------------------------------------------

def test_not_sure_list_is_sorted():
    rules = load_rules()
    cases = {
        ("ApplicationFrameHost.exe", "Settings", ""): "System",
        ("explorer.exe", "", ""): "System",
        ("brave.exe", "New tab", "brave://newtab"): "System",
        ("brave.exe", "Speedtest by Ookla", "https://www.speedtest.net/"): "System",
        ("brave.exe", "Review and sign documents on Docusign | Docusign", "https://apps.docusign.com/send/home"): "System",
        ("brave.exe", "Excalidraw Whiteboard", "https://excalidraw.com/"): "Study tools",
        ("brave.exe", "Add CLAUDE.md · Pull Request #4 · owner/focus-tracker - Brave", ""): "Tech/Work",
        ("brave.exe", "owner/focus-tracker - Brave", ""): "Tech/Work",
        # Not caught by the GitHub title rule: a subreddit, other UWP apps, a known URL.
        ("brave.exe", "r/python", ""): "Other",
        ("brave.exe", "r/python", "https://www.reddit.com/r/python/"): "Entertainment",
        ("ApplicationFrameHost.exe", "Photos", ""): "Other",
    }
    for (process, title, url), want in cases.items():
        assert categorize(rules, process, title, url) == want, (process, title)


def test_system_is_neutral(store):
    assert goal_fit("System", "explorer.exe", "", "", ["leetcode"]) == "Neutral"
    add(store, T0, 10, ("explorer.exe", "Downloads", ""))
    add(store, T0 + 10 * MIN, 10, YT_FUN)
    k = report(store).today()["kpis"]
    assert k["distraction_min"] == 10


# First in / last out -----------------------------------------------------------------

def at(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 11, day, hour, minute).timestamp()


def test_day_edges_per_day_and_typical(store):
    add(store, at(22, 9, 30), 60, CODE)
    add(store, at(22, 22, 0), 60, CODE)  # last out 23:00
    add(store, at(23, 4, 30), 10, LOCK)  # lock screen at 04:30 is not a login
    add(store, at(23, 10, 0), 30, CODE)
    add(store, at(24, 1, 0), 60, CODE)  # after midnight: still the 23rd's day, last out 02:00
    add_state(store, at(23, 10, 0), at(23, 10, 30))
    add(store, at(24, 8, 0), 120, CODE)  # today, still going
    now = at(24, 10, 0) + 30
    e = metrics.day_edges(store, DAY, 4, now, days=4)
    rows = {r["day"]: r for r in e["rows"]}
    assert rows["2026-11-21"]["first"] is None
    assert rows["2026-11-22"]["first"] == at(22, 9, 30) and rows["2026-11-22"]["last"] == at(22, 23, 0)
    assert rows["2026-11-23"]["first"] == at(23, 10, 0) and rows["2026-11-23"]["last"] == at(24, 2, 0)
    assert rows["2026-11-23"]["active_min"] == 30
    assert rows["2026-11-24"]["still_on"] and not rows["2026-11-23"]["still_on"]
    assert e["start"] == {"median": "9:30 AM", "earliest": "8:00 AM", "latest": "10:00 AM", "days": 3}
    # Today's stop is not final, so the usual last out uses the two finished days.
    assert e["stop"] == {"median": "12:30 AM", "earliest": "11:00 PM", "latest": "2:00 AM", "days": 2}


def test_day_edges_skip_future_days_and_clip(store):
    add(store, at(24, 3, 0), 120, CODE)  # 03:00-05:00 crosses the 04:00 day start
    e = metrics.day_edges(store, date(2026, 11, 25), 4, at(24, 12), days=3)
    rows = {r["day"]: r for r in e["rows"]}
    assert "2026-11-25" not in rows
    assert rows["2026-11-23"]["last"] == at(24, 4) and rows["2026-11-24"]["first"] == at(24, 4)


def test_dashboard_today_has_edges_and_system_category(dash, store):  # noqa: F811
    add(store, T0, 30, CODE)
    today = dash.get("/api/today", {})
    assert today["edges"]["rows"][-1]["first"] == T0
    settings = dash.get("/api/settings", {})
    assert "System" in settings["categories"] and settings["neutral"] == ["System"]
    add(store, T0 + 40 * MIN, 5, ("figma.exe", "Design", ""))
    dash.post("/api/tag", {"kind": "app", "key": "figma.exe", "category": "System"})
    assert dash.get("/api/settings", {})["queue"] == []
