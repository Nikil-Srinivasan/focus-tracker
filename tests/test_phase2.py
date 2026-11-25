from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime

import pytest
from conftest import FakeCapturer, FakeSystem
from focus_tracker import browser
from focus_tracker.agent import EXCLUDED_TITLE, Agent
from focus_tracker.browser import BrowserState, sanitize_url, titles_match
from focus_tracker.categorize import (
    categorize,
    goal_fit,
    load_rules,
    parse_keywords,
    parse_rules,
)
from focus_tracker.db import Store
from focus_tracker.system import Foreground
from focus_tracker.titles import clean_title

T0 = datetime(2026, 11, 24, 10, 0, 0).timestamp()
M1 = (0, 0, 1920, 1080)
M2 = (1920, 0, 3840, 1080)


def make(cfg, store, browser_state=None):
    system, cap = FakeSystem(), FakeCapturer()
    agent = Agent(cfg, store, system, lambda: cap, None, browser_state or BrowserState())
    return agent, system, cap


def activity(store):
    return store.query("SELECT start_ts, end_ts, process, title, url FROM activity ORDER BY id")


# titles ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, clean",
    [
        ("(3) WhatsApp - Brave", "WhatsApp"),
        ("Two Sum - LeetCode - Brave - Profile 2", "Two Sum - LeetCode"),
        ("● agent.py - focus-tracker - Visual Studio Code", "agent.py - focus-tracker - Visual Studio Code"),
        ("*notes.txt - Notepad", "notes.txt - Notepad"),
        ("Inbox - Microsoft\u200b Edge", "Inbox"),
        ("Windows PowerShell", "Windows PowerShell"),
    ],
)
def test_clean_title(raw, clean):
    assert clean_title(raw) == clean


# browser bridge ---------------------------------------------------------------

def test_sanitize_url_drops_query_and_credentials():
    assert sanitize_url("https://u:p@example.com:8443/a/b?token=x#f") == "https://example.com:8443/a/b"
    assert sanitize_url("https://example.com/a?q=1", keep_query=True) == "https://example.com/a?q=1"
    assert sanitize_url("brave://settings/privacy") == "brave://settings"


def test_titles_match():
    assert titles_match("Two Sum - LeetCode - Brave", "Two Sum - LeetCode")
    assert titles_match("(2) YouTube - Brave", "YouTube")
    assert not titles_match("Secret page - Brave", "Two Sum - LeetCode")


def report(tab: dict, others: tuple[str, ...] = ()) -> dict:
    """What the extension sends while `tab` is focused."""
    return {"focused": tab, "windowTitles": [tab["title"], *others]}


def test_incognito_tab_is_never_recorded():
    state = BrowserState()
    state.update({"focused": {"url": "https://x.com", "title": "X", "incognito": True}}, now=T0)
    assert state.tab_for_window("X - Brave") is None and not state.is_known_window("X - Brave")


def test_window_titles_without_focus_keep_last_tab():
    state = BrowserState()
    state.update(report({"url": "https://github.com", "title": "GitHub"}), T0)
    state.update({"windowTitles": ["GitHub", "DP lecture"]}, T0 + 5)  # Brave not focused
    assert state.tab_for_window("GitHub - Brave").url == "https://github.com"
    assert state.is_known_window("DP lecture - Brave")
    assert not state.is_known_window("Private thing - Brave")
    with pytest.raises(ValueError):
        state.update({"windowTitles": "nope"}, T0)


@pytest.fixture
def server():
    state = BrowserState()
    srv = browser.start_server(state, 0)  # port 0 = any free port
    yield state, srv.server_address[1]
    srv.shutdown()


def _post(port, body, origin=None, ctype="application/json"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/browser", data=json.dumps(body).encode(), method="POST"
    )
    req.add_header("Content-Type", ctype)
    if origin:
        req.add_header("Origin", origin)
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def test_server_accepts_extension_rejects_web_pages(server):
    state, port = server
    tab = report({"url": "https://leetcode.com/problems/two-sum/", "title": "Two Sum - LeetCode"})
    assert _post(port, tab, origin="https://evil.example") == 403
    assert state.last_seen == 0
    assert _post(port, tab, ctype="text/plain") == 404
    assert _post(port, tab, origin="chrome-extension://abcdef") == 204
    assert state.tab_for_window("Two Sum - LeetCode - Brave").url.startswith("https://leetcode.com")
    assert _post(port, ["not", "an", "object"]) == 400
    assert _post(port, {"windowTitles": 5}) == 400


# agent integration ------------------------------------------------------------

def test_brave_tab_url_recorded_and_title_cleaned(cfg, store):
    state = BrowserState()
    agent, system, _cap = make(cfg, store, state)
    state.update(report({"url": "https://leetcode.com/problems/two-sum/?envType=x", "title": "Two Sum - LeetCode"}), T0)
    system.fg = Foreground("brave.exe", "Two Sum - LeetCode - Brave", M1)
    agent.tick(T0)
    assert activity(store) == [(T0, T0, "brave.exe", "Two Sum - LeetCode", "https://leetcode.com/problems/two-sum/")]


def test_private_brave_window_excluded_once_extension_seen(cfg, store):
    state = BrowserState()
    agent, system, cap = make(cfg, store, state)
    state.update(report({"url": "https://github.com", "title": "GitHub"}), T0)
    system.fg = Foreground("brave.exe", "Something private - Brave", M1)
    agent.tick(T0)
    assert cap.grabs == 0
    assert activity(store)[0][3:] == (EXCLUDED_TITLE, None)


def test_private_brave_window_on_other_monitor_is_not_screenshotted(cfg, store):
    state = BrowserState()
    agent, system, cap = make(cfg, store, state)
    state.update(report({"url": "https://github.com", "title": "GitHub"}, others=("DP lecture",)), T0)
    system.fg = Foreground("code.exe", "main.py - Visual Studio Code", M1)
    system.others = [Foreground("brave.exe", "DP lecture - Brave", M2)]  # normal window
    agent.tick(T0)
    system.others = [Foreground("brave.exe", "Something private - Brave", M2)]
    cap.shade = {1: 90, 2: 250}  # both screens changed
    agent.tick(T0 + cfg.screenshot_interval_s)
    shots = store.query("SELECT ts, monitor FROM screenshot ORDER BY ts, monitor")
    assert shots == [(T0, 1), (T0, 2), (T0 + cfg.screenshot_interval_s, 1)]


def test_without_extension_brave_windows_are_logged_normally(cfg, store):
    agent, system, cap = make(cfg, store)
    system.fg = Foreground("brave.exe", "Docs - Brave", M1)
    agent.tick(T0)
    assert cap.grabs == 1 and activity(store)[0][3] == "Docs"


def test_domain_rules(cfg, store):
    state = BrowserState()
    agent, system, cap = make(cfg, store, state)
    state.update(report({"url": "https://retail.onlinesbi.sbi/login", "title": "State Bank"}), T0)
    system.fg = Foreground("brave.exe", "State Bank - Brave", M1)
    agent.tick(T0)
    assert cap.grabs == 0 and activity(store)[-1][3] == EXCLUDED_TITLE
    state.update(report({"url": "https://lms.vanakkamdsa.com/v2/courses", "title": "Start learning"}), T0 + 10)
    system.fg = Foreground("brave.exe", "Start learning - Brave", M1)
    agent.tick(T0 + 10)
    assert cap.grabs == 0
    assert activity(store)[-1][3:] == ("Start learning", "https://lms.vanakkamdsa.com/v2/courses")


def test_hook_switches_give_exact_times_and_catch_quick_flicks(cfg, store):
    agent, system, _cap = make(cfg, store)
    agent.tick(T0)  # VS Code
    system.switches = [
        (T0 + 0.3, Foreground("WhatsApp.exe", "WhatsApp", M1)),
        (T0 + 0.6, Foreground("Code.exe", "agent.py - focus-tracker", M1)),
    ]
    agent.tick(T0 + 1)
    rows = activity(store)
    assert [(r[0], r[2]) for r in rows] == [(T0, "Code.exe"), (T0 + 0.3, "WhatsApp.exe"), (T0 + 0.6, "Code.exe")]
    assert rows[-1][1] == T0 + 1


def test_switches_ignored_while_locked(cfg, store):
    agent, system, _cap = make(cfg, store)
    system.locked = True
    system.switches = [(T0 + 0.5, Foreground("WhatsApp.exe", "WhatsApp", M1))]
    agent.tick(T0 + 1)
    assert activity(store) == []
    assert system.switches == []  # drained, not replayed after unlock


def test_media_playing_is_watching_not_idle(cfg, store):
    agent, system, cap = make(cfg, store)
    system.idle = cfg.idle_threshold_s + 10
    system.media = True
    agent.tick(T0)
    assert agent.current_state == "watching"
    assert cap.grabs == 1 and len(activity(store)) == 1
    system.media = False
    agent.tick(T0 + 1)
    assert agent.current_state == "idle"


def test_brave_audible_counts_as_watching(cfg, store):
    state = BrowserState()
    agent, system, _cap = make(cfg, store, state)
    system.idle = cfg.idle_threshold_s + 10
    state.update(report({"url": "https://youtube.com/watch", "title": "DP lecture", "audible": True}), T0)
    agent.tick(T0 + 5)
    assert agent.current_state == "watching"
    agent.tick(T0 + 120)  # no fresh heartbeat for 2 min
    assert agent.current_state == "idle"


def test_extension_seen_persists(cfg, store):
    state = BrowserState()
    agent, _system, _cap = make(cfg, store, state)
    state.update(report({"url": "https://github.com", "title": "GitHub"}), T0)
    agent.tick(T0)
    assert float(store.get_meta("extension_last_seen")) == T0


# categories ---------------------------------------------------------------------

def test_default_rules_categorize():
    rules = load_rules(user_path=None)
    assert categorize(rules, "brave.exe", "Start learning", "https://lms.vanakkamdsa.com/v2") == "Learning"
    assert categorize(rules, "brave.exe", "DP tutorial", "https://www.youtube.com/watch") == "Learning"
    # YouTube without a learning title is left to the screen text (Other until it decides).
    assert categorize(rules, "brave.exe", "Funny cats", "https://www.youtube.com/watch") == "Other"
    assert categorize(rules, "Code.exe", "agent.py", "") == "Tech/Work"
    assert categorize(rules, "brave.exe", "Jobs", "https://www.naukri.com/jobs") == "Job search"
    assert categorize(rules, "WhatsApp.exe", "WhatsApp", "") == "Communication"
    assert categorize(rules, "foo.exe", "bar", "") == "Other"


def test_user_rules_win(tmp_path):
    p = tmp_path / "rules.toml"
    p.write_text('[[rule]]\ncategory = "Learning"\nurl = "youtube\\\\.com"\n')
    rules = load_rules(user_path=p)
    assert categorize(rules, "brave.exe", "Funny cats", "https://youtube.com/x") == "Learning"
    with pytest.raises(ValueError):
        parse_rules('[[rule]]\nurl = "x"\n')


def test_goal_fit():
    kw = parse_keywords("focus-tracker, Python ")
    assert kw == ["focus-tracker", "python"]
    assert goal_fit("Tech/Work", "Code.exe", "agent.py - focus-tracker", "", kw) == "On-goal"
    assert goal_fit("Learning", "brave.exe", "Two Sum", "https://leetcode.com", kw) == "Off-goal"
    assert goal_fit("Communication", "WhatsApp.exe", "WhatsApp", "", kw) == "Distraction"
    assert goal_fit("Learning", "brave.exe", "Two Sum", "", []) == "On-goal"


def test_old_database_gets_url_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE activity (id INTEGER PRIMARY KEY, start_ts REAL NOT NULL, end_ts REAL NOT NULL, "
        "process TEXT NOT NULL, title TEXT NOT NULL, monitor INTEGER)"
    )
    con.commit()
    con.close()
    s = Store(path)
    s.open_activity(T0, "a.exe", "t", 1, "https://x.com/")
    assert s.query("SELECT url FROM activity") == [("https://x.com/",)]
    s.close()


def test_store_thread_safety_smoke(store):
    def worker():
        for i in range(50):
            store.set_meta(f"k{i}", "v")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.get_meta("k49") == "v"
