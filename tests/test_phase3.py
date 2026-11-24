"""Phase 3: metrics, dashboard API and server safety, forget range, break prompt, hotkey."""

from __future__ import annotations

import http.client
import json
from datetime import date, datetime

import pytest
from conftest import FakeCapturer, FakeSystem
from focus_tracker import metrics
from focus_tracker.agent import Agent
from focus_tracker.categorize import load_rules
from focus_tracker.config import Config, parse_hotkey
from focus_tracker.dashboard import ApiError, Dashboard, start_dashboard

DAY = date(2026, 11, 24)
T0 = datetime(2026, 11, 24, 10, 0, 0).timestamp()
MIN = 60

CODE = ("Code.exe", "agent.py - focus-tracker - Visual Studio Code", "")
LEETCODE = ("brave.exe", "Two Sum - LeetCode", "https://leetcode.com/problems/two-sum/")
YT_FUN = ("brave.exe", "Funny cats - YouTube", "https://www.youtube.com/watch")
WHATSAPP = ("WhatsApp.exe", "WhatsApp", "")
REDDIT = ("brave.exe", "r/python", "https://www.reddit.com/r/python/")
DOCS = ("brave.exe", "asyncio - Python docs", "https://docs.python.org/3/library/asyncio.html")


def add(store, start, minutes, what, state="active"):
    process, title, url = what
    end = start + minutes * MIN
    store._exec(
        "INSERT INTO activity(start_ts, end_ts, process, title, monitor, url) VALUES(?, ?, ?, ?, 1, ?)",
        (start, end, process, title, url or None),
    )
    return end


def add_state(store, start, end, state="active"):
    store._exec("INSERT INTO state(start_ts, end_ts, state) VALUES(?, ?, ?)", (start, end, state))


def report(store, now=None, rules_path=None):
    classifier = metrics.Classifier(load_rules(rules_path))
    return metrics.DayReport(store, classifier, DAY, 4, now or T0 + 12 * 3600)


# metrics ----------------------------------------------------------------------------

def test_deep_block_survives_short_distraction_and_ends_on_long_one(store):
    t = add(store, T0, 20, CODE)
    t = add(store, t, 0.5, YT_FUN)  # 30 s: allowed inside a block
    t = add(store, t, 15, LEETCODE)
    t = add(store, t, 5, YT_FUN)  # 5 min: ends the block
    add(store, t, 10, CODE)
    add_state(store, T0, t + 10 * MIN)
    r = report(store)
    assert len(r.blocks) == 1
    b = r.blocks[0]
    assert b.start == T0 and round((b.end - b.start) / MIN, 1) == 35.5
    assert b.interrupted_by == "youtube.com"
    today = r.today()
    assert today["kpis"]["deep_blocks"] == 1 and today["kpis"]["deep_min"] == 36
    assert today["kpis"]["distraction_min"] == 6


def test_gap_ends_block_and_short_runs_do_not_count(store):
    t = add(store, T0, 20, CODE)
    add(store, t + 5 * MIN, 20, CODE)  # 5 min away in between
    assert report(store).blocks == []


def test_goal_keywords_make_other_learning_off_goal(store):
    store.set_goal(DAY.isoformat(), T0, "Finish DSA arrays", "leetcode, dsa")
    t = add(store, T0, 30, DOCS)  # learning, but not today's goal
    add(store, t, 30, LEETCODE)
    add_state(store, T0, t + 30 * MIN)
    r = report(store)
    assert [round(b.dur / MIN) for b in r.blocks] == [30]
    assert r.today()["fit"] == {"Off-goal": 30.0, "On-goal": 30.0}
    holes = r.distractions()["rabbit_holes"]
    assert holes == []  # off-goal came first, not after on-goal work


def test_rabbit_hole_after_on_goal_work(store):
    store.set_goal(DAY.isoformat(), T0, "DSA", "leetcode")
    t = add(store, T0, 10, LEETCODE)
    add(store, t, 25, DOCS)
    holes = report(store).distractions()["rabbit_holes"]
    assert holes == [{"start": t, "minutes": 25, "from": "leetcode.com", "sites": ["docs.python.org"]}]


def test_switch_counts_drop_flickers_and_breaks(store):
    t = add(store, T0, 5, CODE)
    t = add(store, t, 1 / 60, WHATSAPP)  # 1 s flicker (alt-tab passing through)
    t = add(store, t, 5, LEETCODE)  # app switch Code -> brave
    t = add(store, t, 5, DOCS)  # tab switch
    add(store, t + 10 * MIN, 5, CODE)  # after a break: not a switch
    add_state(store, T0, t + 15 * MIN)
    apps, tabs = metrics.switch_events(report(store).segs)
    assert len(apps) == 1 and len(tabs) == 1


def test_triggers_and_communication(store):
    t = add(store, T0, 10, CODE)
    t = add(store, t, 3, YT_FUN)
    t = add(store, t, 10, CODE)
    t = add(store, t, 2, WHATSAPP)
    add(store, t, 10, CODE)
    add_state(store, T0, t + 10 * MIN)
    d = report(store).distractions()
    assert d["triggers"][0] == {"before": "Code", "went_to": "youtube.com", "count": 1, "minutes": 3}
    assert d["communication"]["visits"] == 1 and d["communication"]["median_min"] == 2.0


def test_excluded_time_is_private_not_distraction(store):
    add(store, T0, 10, ("brave.exe", "[excluded]", ""))
    r = report(store)
    assert r.segs[0].fit == "Private"
    assert r.today()["kpis"]["distraction_min"] == 0


def test_day_bounds_use_rollover_and_clip(store):
    late = datetime(2026, 11, 25, 3, 50).timestamp()  # still the 24th's tracker day
    add(store, late, 20, CODE)  # runs past 04:00
    segs = report(store).segs
    assert len(segs) == 1 and segs[0].end == datetime(2026, 11, 25, 4, 0).timestamp()


def test_uncategorized_queue(store):
    add(store, T0, 5, ("figma.exe", "Design", ""))
    add(store, T0 + 5 * MIN, 3, ("brave.exe", "Some blog", "https://blog.example.org/post"))
    add(store, T0 + 8 * MIN, 30, CODE)
    q = metrics.uncategorized(store, metrics.Classifier(load_rules()), T0 - 1, T0 + 3600)
    assert [(x["kind"], x["key"]) for x in q] == [("app", "figma.exe"), ("site", "blog.example.org")]


def test_heatmap_spreads_block_over_hours(store):
    add(store, datetime(2026, 11, 24, 9, 40).timestamp(), 40, CODE)
    rows = metrics.heatmap(store, metrics.Classifier(load_rules()), DAY, 4, days=2)["rows"]
    assert rows[-1]["minutes"][9] == 20 and rows[-1]["minutes"][10] == 20
    assert sum(rows[0]["minutes"]) == 0


def test_current_run(store):
    add(store, T0, 10, CODE)
    r = report(store, now=T0 + 10 * MIN + 30)
    assert round(r.current_run() / MIN, 1) == 10.5


# store.delete_range / agent.forget ------------------------------------------------------

def make_agent(cfg, store, break_prompter=None):
    system, cap = FakeSystem(), FakeCapturer()
    return Agent(cfg, store, system, lambda: cap, None, None, break_prompter), system, cap


def test_forget_trims_rows_deletes_files_and_closes_spans(cfg, store):
    agent, _system, cap = make_agent(cfg, store)
    for i in range(0, 601):  # 10 minutes of one window, screenshots every 5 s (changing)
        cap.shade = {1: i % 250, 2: (i * 3) % 250}
        agent.tick(T0 + i)
    files = list(cfg.shots_dir.rglob("*.jpg"))
    assert files
    removed = agent.forget(T0 + 300, T0 + 602)
    assert removed > 0
    rows = store.query("SELECT start_ts, end_ts FROM activity")
    assert rows == [(T0, T0 + 300)]
    left = store.query("SELECT COUNT(*) FROM screenshot WHERE deleted = 0 AND ts >= ?", (T0 + 300,))[0][0]
    assert left == 0
    assert len(list(cfg.shots_dir.rglob("*.jpg"))) == len(files) - removed
    agent.tick(T0 + 603)  # must open a new row, not stretch the old one over the gap
    assert store.query("SELECT start_ts, end_ts FROM activity ORDER BY id") == [(T0, T0 + 300), (T0 + 603, T0 + 603)]


# back-from-break prompt -------------------------------------------------------------------

def test_break_prompt_after_idle(cfg, store):
    asked = []

    def prompter(away):
        asked.append(away)
        return "Thinking"

    agent, system, _cap = make_agent(cfg, store, prompter)
    agent.tick(T0)
    system.idle = cfg.idle_threshold_s
    agent.tick(T0 + 300)  # idle noticed; the break began at the last input (T0)
    system.idle = cfg.idle_threshold_s + 400
    agent.tick(T0 + 700)
    system.idle = 0
    agent.tick(T0 + 701)
    for t in __import__("threading").enumerate():
        if t.name == "break":
            t.join(2)
    assert len(asked) == 1 and round(asked[0]) == 701
    assert store.query("SELECT start_ts, end_ts, label FROM break_label") == [(T0, T0 + 701, "Thinking")]


def test_short_lock_does_not_prompt(cfg, store):
    asked = []
    agent, system, _cap = make_agent(cfg, store, lambda away: asked.append(away))
    agent.tick(T0)
    system.locked = True
    agent.tick(T0 + 1)
    system.locked = False
    agent.tick(T0 + 60)
    assert asked == []


# hotkey parsing --------------------------------------------------------------------------

def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+p") == (0x3, ord("P"))
    assert parse_hotkey("") is None
    for bad in ("p", "ctrl+", "ctrl+alt+F12", "hyper+p"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)
    with pytest.raises(ValueError):
        Config(dashboard_port=5610).validate()


# dashboard API -----------------------------------------------------------------------------

@pytest.fixture
def dash(cfg, store, tmp_path):
    agent, _system, _cap = make_agent(cfg, store)
    return Dashboard(cfg, store, agent, rules_path=tmp_path / "rules.toml", clock=lambda: T0 + 3600)


def test_api_pages_return_data(dash, store):
    add(store, T0, 30, CODE)
    add_state(store, T0, T0 + 30 * MIN)
    today = dash.get("/api/today", {})
    assert today["day"] == "2026-11-24" and today["today"] == "2026-11-24"
    assert today["kpis"]["deep_min"] == 30
    focus = dash.get("/api/focus", {"day": "2026-11-24"})
    assert len(focus["heatmap"]["rows"]) == 28
    assert dash.get("/api/distractions", {})["distraction"] == []
    assert dash.get("/api/settings", {})["categories"]
    with pytest.raises(ApiError):
        dash.get("/api/today", {"day": "not-a-day"})


def test_tag_writes_user_rule_and_recategorizes(dash, store):
    add(store, T0, 5, ("figma.exe", "Design", ""))
    add(store, T0 + 5 * MIN, 5, ("brave.exe", "Post", "https://blog.example.org/x"))
    assert len(dash.get("/api/settings", {})["queue"]) == 2
    dash.post("/api/tag", {"kind": "app", "key": "figma.exe", "category": "Tech/Work"})
    dash.post("/api/tag", {"kind": "site", "key": "example.org", "category": "Learning"})
    assert dash.get("/api/settings", {})["queue"] == []
    cats = {c["label"] for c in dash.get("/api/today", {})["categories"]}
    assert {"Tech/Work", "Learning"} <= cats
    # Subdomain match only on a dot boundary.
    rules = load_rules(dash.rules_path)
    assert metrics.categorize(rules, "brave.exe", "x", "https://notexample.org/") == "Uncategorized"
    for bad in (
        {"kind": "site", "key": "evil.com'\ncategory='x", "category": "Learning"},
        {"kind": "app", "key": "x.exe", "category": "Nope"},
        {"kind": "app", "key": "../x", "category": "Learning"},
    ):
        with pytest.raises(ApiError):
            dash.post("/api/tag", bad)


def test_replay_and_shot_path(dash, cfg, store):
    agent = dash.agent
    agent.tick(T0)
    frames = dash.get("/api/replay", {})
    assert [f[1] for f in frames["frames"]] == [1, 2]
    path = dash.shot_path(frames["frames"][0][2])
    assert path.is_file()
    store._exec("INSERT INTO screenshot(ts, monitor, path, bytes) VALUES(?, 1, '../../config.toml', 1)", (T0,))
    bad_id = store.query("SELECT MAX(id) FROM screenshot")[0][0]
    with pytest.raises(ApiError):
        dash.shot_path(bad_id)


# server safety --------------------------------------------------------------------------------

@pytest.fixture
def server(dash):
    srv = start_dashboard(dash, 0)
    yield dash, srv.server_address[1]
    srv.shutdown()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    hdrs.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hdrs)
    res = conn.getresponse()
    return res.status, dict(res.getheaders()), res.read()


def test_server_serves_page_with_token_and_headers(server):
    dash, port = server
    status, headers, body = request(port, "GET", "/")
    assert status == 200 and dash.token.encode() in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert request(port, "GET", "/app.js")[0] == 200
    assert request(port, "GET", "/api/today")[0] == 200


def test_server_blocks_dns_rebinding_and_cross_site_posts(server):
    dash, port = server
    assert request(port, "GET", "/api/today", headers={"Host": f"evil.example:{port}"})[0] == 403
    tag = {"kind": "app", "key": "figma.exe", "category": "Tech/Work"}
    assert request(port, "POST", "/api/tag", tag)[0] == 403  # no token
    assert request(port, "POST", "/api/tag", tag, {"X-Focus-Token": "wrong"})[0] == 403
    assert request(port, "POST", "/api/tag", tag,
                   {"X-Focus-Token": dash.token, "Origin": "https://evil.example"})[0] == 403
    assert request(port, "POST", "/api/tag", tag,
                   {"X-Focus-Token": dash.token, "Content-Type": "text/plain"})[0] == 415
    assert not dash.rules_path.exists()
    ok = request(port, "POST", "/api/tag", tag,
                 {"X-Focus-Token": dash.token, "Origin": f"http://127.0.0.1:{port}"})
    assert ok[0] == 200 and dash.rules_path.exists()


def test_server_delete_needs_token(server, store):
    dash, port = server
    add(store, T0 + 3000, 5, CODE)
    assert request(port, "POST", "/api/delete", {"range": "hour"})[0] == 403
    status, _h, body = request(port, "POST", "/api/delete", {"range": "hour"}, {"X-Focus-Token": dash.token})
    assert status == 200 and json.loads(body)["ok"]
    assert store.query("SELECT COUNT(*) FROM activity")[0][0] == 0
