"""Editing settings from the dashboard: validate, save to config.toml, apply live."""

from __future__ import annotations

import http.client
import json
import re
import tomllib
from datetime import datetime
from importlib import resources

import pytest
from conftest import FakeCapturer, FakeSystem
from focus_tracker import plan, retention
from focus_tracker.agent import Agent
from focus_tracker.categorize import load_rules
from focus_tracker.config import load_config
from focus_tracker.dashboard import ApiError, Dashboard, start_dashboard
from focus_tracker.metrics import Classifier

NOW = datetime(2026, 11, 26, 11, 0, 0).timestamp()  # Thursday


@pytest.fixture
def agent(cfg, store):
    return Agent(cfg, store, FakeSystem(), lambda: FakeCapturer(), None, None, None)


@pytest.fixture
def conf(tmp_path):
    return tmp_path / "config.toml"


@pytest.fixture
def hotkeys():
    return []


@pytest.fixture
def dash(cfg, store, agent, tmp_path, conf, hotkeys):
    return Dashboard(cfg, store, agent, rules_path=tmp_path / "rules.toml", clock=lambda: NOW,
                     config_path=conf, apply_hotkey=hotkeys.append)


def save(dash, **edits):
    return dash.post("/api/settings", edits)


def test_saves_to_file_and_applies_live(dash, cfg, conf, tmp_path):
    conf.write_text('data_dir = "elsewhere"\njpeg_quality = 70\n', encoding="utf-8")
    r = save(dash, screenshot_interval_s=10, idle_threshold_s=600, day_rollover_hour=5,
             max_storage_gb=20, ask_daily_goal=False, deep_work_goal_h=6.5)
    assert r["changed"] == sorted(["screenshot_interval_s", "idle_threshold_s", "day_rollover_hour",
                                   "max_storage_gb", "ask_daily_goal", "deep_work_goal_h"])
    assert (cfg.screenshot_interval_s, cfg.idle_threshold_s, cfg.day_rollover_hour) == (10, 600, 5)
    assert cfg.ask_daily_goal is False and cfg.deep_work_goal_h == 6.5
    raw = tomllib.loads(conf.read_text(encoding="utf-8"))
    assert raw["data_dir"] == "elsewhere" and raw["jpeg_quality"] == 70  # other keys kept
    assert raw["screenshot_interval_s"] == 10 and raw["max_storage_gb"] == 20
    reloaded = load_config(conf)  # the written file loads and validates
    assert reloaded.idle_threshold_s == 600 and reloaded.day_rollover_hour == 5
    assert not (tmp_path / "config.toml.tmp").exists()
    assert dash.get("/api/settings", {})["values"]["screenshot_interval_s"] == 10


def test_unchanged_values_write_nothing(dash, conf):
    assert save(dash, screenshot_interval_s=5, idle_threshold_s=300)["changed"] == []
    assert not conf.exists()


@pytest.mark.parametrize("edits", [
    {"screenshot_interval_s": 0},
    {"screenshot_interval_s": "5"},
    {"screenshot_interval_s": True},
    {"idle_threshold_s": 10},
    {"max_storage_gb": 0},
    {"day_rollover_hour": 24},
    {"day_rollover_hour": 4.5},
    {"wipe_weekday": 7},
    {"wipe_time": "25:00"},
    {"wipe_time": "9:00"},
    {"pause_hotkey": "p"},
    {"pause_hotkey": "ctrl+alt+f12"},
    {"ask_daily_goal": "yes"},
    {"data_dir": "C:/anywhere"},
    {"dashboard_port": 9000},
    {"store_full_url": True},
    {"exclude_domains": "paytm.com"},
    {"exclude_domains": ["onlinesbi.sbi", "sbi.co.in", "icicibank.com", "razorpay.com", "paytm.com",
                         "phonepe.com", "pay.google.com", "not a domain"]},
    {"exclude_processes": ["notepad"]},
    {"no_screenshot_title_keywords": ["vanakkamdsa", "vanakkam dsa", "ab"]},
])
def test_rejects_bad_values_and_changes_nothing(dash, cfg, conf, edits):
    before = (cfg.screenshot_interval_s, cfg.idle_threshold_s, list(cfg.exclude_domains), cfg.pause_hotkey)
    conf.write_text("jpeg_quality = 70\n", encoding="utf-8")
    with pytest.raises(ApiError) as err:
        save(dash, screenshot_interval_s=9, **edits) if "screenshot_interval_s" not in edits else save(dash, **edits)
    assert err.value.status == 400
    assert (cfg.screenshot_interval_s, cfg.idle_threshold_s, list(cfg.exclude_domains), cfg.pause_hotkey) == before
    assert conf.read_text(encoding="utf-8") == "jpeg_quality = 70\n"


def test_lists_add_normalise_and_keep_built_ins(dash, cfg):
    domains = list(cfg.exclude_domains) + [" WWW.MyBank.co.in ", "mybank.co.in", ""]
    save(dash, exclude_domains=domains, exclude_title_keywords=list(cfg.exclude_title_keywords) + ["HDFC"])
    assert cfg.exclude_domains[-1] == "mybank.co.in" and cfg.exclude_domains.count("mybank.co.in") == 1
    assert cfg.exclude_title_keywords[-1] == "hdfc"
    # Your own entries can be removed again ...
    save(dash, exclude_domains=[d for d in cfg.exclude_domains if d != "mybank.co.in"])
    assert "mybank.co.in" not in cfg.exclude_domains
    # ... built-in protections cannot (private windows, banks, the course site).
    for key, item in [("exclude_title_keywords", "incognito"), ("exclude_domains", "paytm.com"),
                      ("no_screenshot_domains", "lms.vanakkamdsa.com"), ("exclude_processes", "1password.exe")]:
        with pytest.raises(ApiError, match="built-in"):
            save(dash, **{key: [x for x in getattr(cfg, key) if x != item]})
        assert item in getattr(cfg, key)
    s = dash.get("/api/settings", {})
    assert "incognito" in s["built_in"]["exclude_title_keywords"] and "hdfc" not in s["built_in"]["exclude_title_keywords"]


def test_new_privacy_entries_take_effect_on_the_next_tick(dash, cfg, store, agent):
    from focus_tracker.system import Foreground
    agent.system.fg = Foreground("brave.exe", "HDFC Bank - NetBanking", (0, 0, 1920, 1080))
    save(dash, exclude_title_keywords=list(cfg.exclude_title_keywords) + ["hdfc"])
    agent.tick(NOW)
    titles = [r[0] for r in store.query("SELECT title FROM activity")]
    assert titles == ["[excluded]"]
    assert store.query("SELECT COUNT(*) FROM screenshot")[0][0] == 0


def test_moving_the_wipe_never_wipes_on_the_spot(dash, cfg, store, agent, conf, tmp_path):
    store.set_meta(retention.LAST_WIPE_KEY, str(datetime(2026, 11, 22, 23, 59).timestamp()))  # last Sunday
    shot_ts = datetime(2026, 11, 24, 10, 0).timestamp()  # Tuesday
    rel = "2026-11-24/10/100000_m1.jpg"
    (cfg.shots_dir / rel).parent.mkdir(parents=True)
    (cfg.shots_dir / rel).write_bytes(b"x")
    store._exec("INSERT INTO screenshot(ts, monitor, path, bytes, deleted) VALUES(?, 1, ?, 1, 0)", (shot_ts, rel))
    save(dash, wipe_weekday=2, wipe_time="09:30")  # Wednesday 9:30 AM, already past this week
    assert (cfg.wipe_weekday, cfg.wipe_time) == (2, "09:30")
    assert retention.maybe_wipe(store, cfg, NOW + 5) == 0
    assert (cfg.shots_dir / rel).exists()
    classifier = lambda: Classifier(load_rules(tmp_path / "none.toml"))  # noqa: E731
    store._exec("INSERT INTO activity(start_ts, end_ts, process, title, monitor) VALUES(?, ?, 'Code.exe', 'x', 1)",
                (shot_ts, shot_ts + 600))
    assert plan.maybe_generate(store, classifier, cfg, NOW + 5) is None  # no plan for the skipped Wednesday
    next_wed = datetime(2026, 12, 2, 9, 31).timestamp()
    assert retention.maybe_wipe(store, cfg, next_wed) == 1  # the new schedule runs from next week
    assert plan.maybe_generate(store, classifier, cfg, next_wed) is not None
    assert tomllib.loads(conf.read_text(encoding="utf-8"))["wipe_time"] == "09:30"


def test_hotkey_is_normalised_and_reregistered(dash, cfg, hotkeys):
    save(dash, pause_hotkey=" Ctrl + Shift + 9 ")
    assert cfg.pause_hotkey == "ctrl+shift+9" and hotkeys == ["ctrl+shift+9"]
    save(dash, pause_hotkey="")
    assert cfg.pause_hotkey == "" and hotkeys[-1] == ""
    save(dash, idle_threshold_s=120)
    assert len(hotkeys) == 2  # only called when the hotkey changes


def test_works_without_a_running_agent(cfg, store, tmp_path):
    dash = Dashboard(cfg, store, None, rules_path=tmp_path / "r.toml", config_path=tmp_path / "c.toml")
    dash.post("/api/settings", {"screenshot_interval_s": 15})
    assert cfg.screenshot_interval_s == 15


def test_server_needs_token_and_takes_full_list_bodies(dash, cfg, conf):
    srv = start_dashboard(dash, 0)
    port = srv.server_address[1]
    try:
        def post(body, token):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            data = json.dumps(body).encode()
            hdrs = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
            if token:
                hdrs["X-Focus-Token"] = token
            conn.request("POST", "/api/settings", body=data, headers=hdrs)
            res = conn.getresponse()
            return res.status, res.read()

        assert post({"screenshot_interval_s": 9}, None)[0] == 403
        assert post({"screenshot_interval_s": 9}, "wrong")[0] == 403
        assert not conf.exists()
        big = list(cfg.exclude_title_keywords) + [f"keyword number {i}" for i in range(150)]
        status, body = post({"exclude_title_keywords": big}, dash.token)  # well over the old 4 KB limit
        assert status == 200, body
        assert len(cfg.exclude_title_keywords) == len(big)
        assert post({"exclude_title_keywords": ["k" * 90] * 190}, dash.token)[0] == 413  # over 16 KB
    finally:
        srv.shutdown()


def test_page_uses_in_app_dialogs_and_no_unsafe_html():
    js = resources.files("focus_tracker").joinpath("web", "app.js").read_text("utf-8")
    assert not re.search(r"\b(confirm|alert|prompt)\(", js)
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    html = resources.files("focus_tracker").joinpath("web", "index.html").read_text("utf-8")
    assert 'src="/theme.js"' in html and "<dialog" in html
