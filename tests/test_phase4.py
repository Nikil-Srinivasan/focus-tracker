"""Phase 4: masking, the no-flash Claude client, the weekly plan engine and its API."""

from __future__ import annotations

import json
import stat
import sys
import time
from datetime import date, datetime, timedelta

import pytest
from focus_tracker import plan, review
from focus_tracker.categorize import load_rules
from focus_tracker.dashboard import ApiError, Dashboard
from focus_tracker.masking import is_sensitive, redact_text
from focus_tracker.metrics import Classifier

CODE = ("Code.exe", "agent.py - focus-tracker - Visual Studio Code", None)
YT = ("brave.exe", "Funny cats - YouTube", "https://www.youtube.com/watch")
WA = ("WhatsApp.exe", "WhatsApp", None)
MIN = 60
SUN = date(2026, 11, 22)  # a Sunday: the week Mon 16 .. Sun 22


def add(store, start, minutes, what):
    store._exec(
        "INSERT INTO activity(start_ts, end_ts, process, title, monitor, url) VALUES(?, ?, ?, ?, 1, ?)",
        (start, start + minutes * MIN, *what),
    )
    return start + minutes * MIN


def day_of_work(store, d: date, yt_visits=4, yt_min=8, slow_start_min=60, comm=0):
    """A day: (slow) start on YouTube, then code with YouTube breaks."""
    t0 = datetime(d.year, d.month, d.day, 9, 0).timestamp()
    t = add(store, t0, slow_start_min, YT) if slow_start_min else t0
    for _ in range(yt_visits):
        t = add(store, t, 40, CODE)
        t = add(store, t, yt_min, YT)
    for _ in range(comm):
        t = add(store, t, 10, CODE)
        t = add(store, t, 1, WA)
    t = add(store, t, 40, CODE)
    store._exec("INSERT INTO state(start_ts, end_ts, state) VALUES(?, ?, 'active')", (t0, t))


def build(store, end_day=SUN, **kw):
    return plan.build_plan(store, Classifier(load_rules()), end_day, 4, time.time(), **kw)


# masking --------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "ANTHROPIC_API_KEY=sk-ant-api03-abc123def456ghi789jklmno",
    "mongodb://svc_user:S3cretPass99@cluster0.example.net/db",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIi.abc123",
    "Card 4111 1111 1111 1111 exp 12/29",
    "Aadhaar 2345 6789 0123",
    "PAN ABCDE1234F",
    "IFSC SBIN0001234",
    "Pay to someone.dev@okaxis now",
    "Your OTP is 482913",
    "Call +91 9876543210",
    "A/c No: 123456789012",
    "mail me at someone@example.com",
])
def test_redact_masks_secrets_and_indian_ids(text):
    red, hits = redact_text(text)
    assert hits >= 1 and "[MASKED]" in red


@pytest.mark.parametrize("text", [
    "Two Sum - LeetCode",
    "agent.py - focus-tracker - Visual Studio Code",
    "youtube.com took 4 h 10 min across 5 of 5 tracked days",
    "Order #48213",
    "Week ending 2026-11-22: 3.5 h/day",
])
def test_redact_leaves_normal_text(text):
    assert redact_text(text) == (text, 0)


def test_card_check_uses_luhn():
    from focus_tracker.masking import _mask_cards

    assert _mask_cards("4111 1111 1111 1111")[1] == 1
    assert _mask_cards("4111 1111 1111 1112")[1] == 0


def test_is_sensitive_handles_ocr_spacing():
    assert is_sensitive("mongodb : / / user : pass @ host")


# claude client ------------------------------------------------------------------------------

@pytest.fixture
def fake_claude(tmp_path):
    """A stand-in for claude.exe that echoes stdin length in a JSON envelope."""
    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, json, os\n"
        "data = sys.stdin.read()\n"
        "if 'FAIL' in data:\n"
        "    print(json.dumps({'is_error': True, 'result': 'Invalid API key (401)'})); sys.exit(1)\n"
        "print(json.dumps({'result': 'got ' + str(len(data)) + ' chars; args=' + ' '.join(sys.argv[1:])}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.mark.skipif(sys.platform == "win32", reason="shebang script stands in for claude.exe")
def test_run_claude_uses_stdin_and_json_envelope(fake_claude):
    out = review.run_claude("hello & | ^ %", fake_claude, timeout=30)
    assert out == "got 13 chars; args=-p --output-format json"  # prompt never on the command line
    with pytest.raises(RuntimeError, match="sign in again"):
        review.run_claude("FAIL", fake_claude, timeout=30)


def test_find_claude_rejects_missing_configured_path(tmp_path):
    assert review.find_claude(str(tmp_path / "nope.exe")) is None
    with pytest.raises(RuntimeError, match="not found"):
        review.run_claude("x", str(tmp_path / "nope.exe"))


def test_review_input_is_aggregated_and_redacted(store):
    for i in range(5):
        day_of_work(store, SUN - timedelta(days=i))
    p = build(store)
    p["actions"][0]["finding"] += " token=supersecretvalue"
    sent = review.review_input(p)
    assert "supersecretvalue" not in sent and "[MASKED]" in sent
    assert "https://" not in sent  # labels are domains, never URLs
    assert "segs" not in sent and "other_findings" not in sent


# plan engine ---------------------------------------------------------------------------------

def test_plan_ranks_top_sink_and_slow_start(store):
    for i in range(5):
        day_of_work(store, SUN - timedelta(days=i))
    p = build(store)
    assert p["tracked_days"] == 5 and p["confidence"] == "ok"
    checks = [a["check"] for a in p["actions"]]
    assert checks[0] == "top_sink"
    assert "slow_start" in checks
    top = p["actions"][0]
    assert top["target"] == {"metric": "site_minutes", "key": "youtube.com", "op": "<=", "value": 230,
                             "unit": "min", "baseline": 460}
    assert top["finding"].startswith("youtube.com took 7 h 40 min across 5 of 5 tracked days")
    assert p["deep"]["target_h"] is None  # still measuring the baseline


def test_low_confidence_with_few_days(store):
    day_of_work(store, SUN)
    p = build(store)
    assert p["tracked_days"] == 1 and p["confidence"] == "low"
    assert "1 tracked day." in p["confidence_note"]


def test_empty_week_has_no_actions(store):
    p = build(store)
    assert p["tracked_days"] == 0 and p["actions"] == []


def test_comm_pull_check(store):
    for i in range(5):
        day_of_work(store, SUN - timedelta(days=i), yt_visits=0, slow_start_min=0, comm=12)
    checks = [a["check"] for a in build(store)["actions"]]
    assert "comm_pull" in checks


def save(store, p):
    store.save_plan(p["end_day"], p["created"], json.dumps(p))


def test_grading_and_deep_target_next_week(store):
    for i in range(5):
        day_of_work(store, SUN - timedelta(days=i))
    week1 = build(store)
    save(store, week1)
    nxt = SUN + timedelta(days=7)
    for i in range(5):  # straight into work, YouTube breaks cut to a quarter
        day_of_work(store, nxt - timedelta(days=i), yt_visits=4, yt_min=2, slow_start_min=0)
    week2 = build(store, end_day=nxt)
    graded = {g["check"]: g for g in week2["graded"]}
    assert graded["top_sink"]["result"] == "hit" and graded["top_sink"]["actual"] == 40
    assert graded["slow_start"]["result"] == "hit"
    assert week2["deep"]["target_h"] == round(week2["deep_h_per_day"] + 0.5, 1)


def test_missed_twice_swaps_the_action(store):
    weeks = [SUN + timedelta(days=7 * k) for k in range(3)]
    for end in weeks:
        for i in range(5):
            day_of_work(store, end - timedelta(days=i))  # nothing changes: every target missed
        p = build(store, end_day=end)
        save(store, p)
    assert "top_sink" in p["skipped_checks"]
    assert all(a["check"] != "top_sink" for a in p["actions"])


def test_maybe_generate_once_per_week(store, cfg):
    day_of_work(store, SUN - timedelta(days=1))
    now = datetime(2026, 11, 23, 10, 0).timestamp()  # Monday after the Sunday 23:59 boundary
    made = plan.maybe_generate(store, lambda: Classifier(load_rules()), cfg, now)
    assert made and made["end_day"] == "2026-11-22"
    assert plan.maybe_generate(store, lambda: Classifier(load_rules()), cfg, now + 60) is None


def test_maybe_generate_skips_weeks_before_first_data(store, cfg):
    day_of_work(store, SUN + timedelta(days=1))  # first data on Monday the 23rd
    now = datetime(2026, 11, 24, 10, 0).timestamp()
    assert plan.maybe_generate(store, lambda: Classifier(load_rules()), cfg, now) is None


def test_agent_saves_plan_on_tick(cfg, store):
    from conftest import FakeCapturer, FakeSystem
    from focus_tracker.agent import Agent

    day_of_work(store, SUN)
    agent = Agent(cfg, store, FakeSystem(), lambda: FakeCapturer(), None)
    agent.tick(datetime(2026, 11, 23, 10, 0).timestamp())
    assert store.plan_days() == ["2026-11-22"]


# dashboard ----------------------------------------------------------------------------------

def test_plan_api_and_review(cfg, store, tmp_path):
    for i in range(3):
        day_of_work(store, SUN - timedelta(days=i))
    save(store, build(store))
    dash = Dashboard(cfg, store, None, rules_path=tmp_path / "rules.toml",
                     clock=lambda: datetime(2026, 11, 23, 12, 0).timestamp())
    sent = []
    dash.review_fn = lambda p, path, timeout: sent.append(p) or "1. Cut YouTube."
    d = dash.get("/api/plan", {})
    assert d["weeks"] == ["2026-11-22"] and d["saved"]["end_day"] == "2026-11-22"
    assert d["preview"]["end_day"] == "2026-11-23"
    assert dash.post("/api/plan/review", {"week": "2026-11-22"})["status"] == "running"
    for _ in range(50):
        if store.get_plan("2026-11-22")[2]:
            break
        time.sleep(0.05)
    assert store.get_plan("2026-11-22")[2] == "1. Cut YouTube."
    assert len(sent) == 1
    with pytest.raises(ApiError):
        dash.post("/api/plan/review", {"week": "2026-11-22"})  # already reviewed
    with pytest.raises(ApiError):
        dash.post("/api/plan/review", {"week": "2026-01-04"})  # no such plan
