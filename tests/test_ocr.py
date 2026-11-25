"""Screen-text reading (OCR) of saved screenshots: verdicts, privacy, deletion."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pytest
from conftest import FakeCapturer, FakeSystem
from focus_tracker import ocr, retention
from focus_tracker.agent import Agent
from focus_tracker.categorize import (
    categorize_with_content,
    goal_fit,
    load_content,
    load_rules,
    parse_content,
    parse_rules,
)
from focus_tracker.config import Config
from focus_tracker.metrics import Classifier, day_bounds, load_segments
from focus_tracker.ocr import ContentMatcher, OcrWorker, _Tally, goal_on_screen
from focus_tracker.system import Foreground

T0 = datetime(2026, 11, 24, 10, 0, 0).timestamp()  # a Tuesday
DAY = "2026-11-24"

DSA_PAGE = """Dynamic Programming full course for beginners - YouTube
take U forward  1.2M subscribers
In this lecture we cover memoization, recursion and time complexity with arrays
Recommended: Funny cat compilation"""
VLOG_PAGE = """My Goa travel vlog | best beach food
Official music video
Recommended: prank gone wrong, comedy reaction, song remix"""
SECRET_LINE = "otp 482913 for card ending"


class FakeEngine:
    """Returns a canned text per call and records the files it was asked to read."""

    def __init__(self, text: str = DSA_PAGE):
        self.text = text
        self.read_paths: list[Path] = []
        self.fail: Exception | None = None

    def read(self, path: Path) -> str:
        if self.fail is not None:
            raise self.fail
        assert path.is_file()  # always a saved screenshot file, never the live screen
        self.read_paths.append(path)
        return self.text


def youtube(title: str, monitor_left: int = 0) -> Foreground:
    return Foreground("brave.exe", f"{title} - YouTube - Brave", (monitor_left, 0, 1920, 1080))


def make(cfg, store, engine: FakeEngine | None = None):
    engine = engine or FakeEngine()
    worker = OcrWorker(store, engine, lambda: load_content(Path("/nonexistent/rules.toml")))
    system, cap = FakeSystem(), FakeCapturer()
    agent = Agent(cfg, store, system, lambda: cap, None, ocr=worker)
    return agent, system, cap, worker, engine


def drain(worker: OcrWorker) -> int:
    n = 0
    while not worker._queue.empty():
        worker.process(worker._queue.get_nowait())
        n += 1
    return n


def content_rows(store):
    return store.query("SELECT title, content_cat, content_terms, content_goal FROM activity ORDER BY id")


def segments(store, keywords=()):
    lo, hi = day_bounds(date(2026, 11, 24), 4)
    return load_segments(store, Classifier(load_rules(Path("/nonexistent/rules.toml"))), lo, hi, list(keywords))


# --- word lists and verdicts --------------------------------------------------------


def defaults() -> ContentMatcher:
    return ContentMatcher(load_content(Path("/nonexistent/rules.toml")))


def test_dsa_page_is_learning_and_vlog_is_entertainment():
    cat, terms = defaults().classify(DSA_PAGE)
    assert cat == "Learning"
    assert {"dynamic programming", "memoization", "recursion", "array", "lecture"} <= set(terms)
    cat, terms = defaults().classify(VLOG_PAGE)
    assert cat == "Entertainment"
    assert "vlog" in terms or "travel vlog" in terms


def test_too_few_or_mixed_words_decide_nothing():
    m = defaults()
    assert m.classify("Home - YouTube  Shorts  Gaming  Movies  Podcasts  Courses") == (None, [])
    assert m.classify("python tutorial") == (None, [])  # only 2 words
    # 3 learning vs 2 entertainment: not a clear lead
    assert m.classify("python tutorial lecture | funny prank")[0] is None


def test_phrases_count_once_and_plurals_and_symbols_match():
    m = ContentMatcher({"Learning": ["crash course", "course", "array", "c++"], "Other": ["zzz"]})
    hits = m._hits("Learning", "C++ CRASH  COURSE on Arrays")
    assert hits == {"c++", "crash course", "array"}
    assert m._hits("Learning", "arrayed courseware") == set()


def test_goal_keywords_match_whole_words_only():
    assert goal_on_screen("Graph BFS template", ["bfs", "dp"])
    assert not goal_on_screen("Display 96 dpi", ["dp"])
    assert not goal_on_screen("anything", [])


def test_tally_counts_productive_votes_together():
    t = _Tally()
    t.add("Learning", ["dsa", "lecture", "array"], False)
    t.add("Tech/Work", ["import", "def", "pytest"], False)
    t.add("Learning", ["recursion", "lecture", "dsa"], False)
    t.add("Entertainment", ["vlog", "song", "prank"], False)
    cat, terms, _ = t.verdict()
    assert cat == "Learning" and "import" not in terms  # words of the winner only


def test_tally_needs_a_majority_of_deciding_frames():
    t = _Tally()
    t.add("Learning", ["dsa", "array", "lecture"], False)
    t.add(None, [], False)  # full-screen talking head decides nothing
    t.add(None, [], False)
    assert t.verdict()[0] == "Learning"
    t.add("Entertainment", ["vlog", "song", "prank"], False)
    assert t.verdict()[0] is None  # 1 vs 1
    t.add("Learning", ["dsa"], True)
    cat, terms, goal = t.verdict()
    assert cat == "Learning" and terms.startswith("dsa")
    assert goal is False  # 1 goal frame of 5
    t.add(None, [], True)
    assert t.verdict()[2] is True  # 2 of 6 = a third


# --- rules ---------------------------------------------------------------------------


def test_your_rules_then_screen_text_then_built_in_rules_then_other(tmp_path):
    user = tmp_path / "rules.toml"
    user.write_text('[[rule]]\ncategory = "Learning"\nurl = "reddit\\\\.com/r/learnprogramming"\n', encoding="utf-8")
    rules = load_rules(user)
    yt = ("brave.exe", "Lofi beats", "https://www.youtube.com/watch")
    # YouTube with no confident screen text is Other: it can be either.
    assert categorize_with_content(rules, *yt, None) == "Other"
    assert categorize_with_content(rules, *yt, "Learning") == "Learning"
    assert categorize_with_content(rules, *yt, "Entertainment") == "Entertainment"
    # The screen text beats a built-in rule, both ways.
    assert categorize_with_content(rules, "brave.exe", "x", "https://www.reddit.com/r/python", "Learning") == "Learning"
    assert categorize_with_content(rules, "brave.exe", "x", "https://www.reddit.com/r/memes", None) == "Entertainment"
    assert categorize_with_content(rules, "Code.exe", "main.py", "", None) == "Tech/Work"
    # Your rule beats the screen text.
    mine = ("brave.exe", "x", "https://www.reddit.com/r/learnprogramming/")
    assert categorize_with_content(rules, *mine, "Entertainment") == "Learning"
    # Unknown app, nothing on screen decided: Other.
    assert categorize_with_content(rules, "SumatraPDF.exe", "notes.pdf", "", None) == "Other"
    assert categorize_with_content(rules, "SumatraPDF.exe", "notes.pdf", "", "Learning") == "Learning"


def test_two_productive_lists_do_not_cancel_out():
    m = defaults()
    text = "Python tutorial: import json, def parse, pytest unit test, recursion lecture"
    cat, _ = m.classify(text)
    assert cat in {"Learning", "Tech/Work"}
    # but productive vs not still needs a clear lead
    assert m.classify("python tutorial lecture, funny prank reaction")[0] is None


def test_job_and_chat_pages_are_recognised():
    m = defaults()
    assert m.classify("Easy Apply · 2-4 years of experience · Salary 12 LPA · Recruiter")[0] == "Job search"
    assert m.classify("Type a message   last seen today   Archived chats   Unread")[0] == "Communication"


def test_goal_on_screen_makes_productive_time_on_goal_only():
    assert goal_fit("Tech/Work", "Code.exe", "main.py", "", ["graph"]) == "Off-goal"
    assert goal_fit("Tech/Work", "Code.exe", "main.py", "", ["graph"], on_screen=True) == "On-goal"
    assert goal_fit("Entertainment", "brave.exe", "x", "", ["graph"], on_screen=True) == "Distraction"


def test_user_content_lists_merge_and_bad_shapes_are_rejected(tmp_path):
    user = tmp_path / "rules.toml"
    user.write_text('[[content]]\ncategory = "Learning"\nwords = ["Striver", "takeuforward"]\n', encoding="utf-8")
    lists = load_content(user)
    assert "takeuforward" in lists["Learning"]
    assert lists["Learning"].count("striver") == 1  # already a default word
    assert "vlog" in lists["Entertainment"]
    with pytest.raises(ValueError):
        parse_content('[[content]]\ncategory = "Learning"\nwords = "dsa"\n')
    assert all(r.user for r in parse_rules('[[rule]]\ncategory = "Learning"\nurl = "x"\n', user=True))


def test_ocr_config_must_be_a_bool(tmp_path):
    from focus_tracker.config import load_config

    path = tmp_path / "config.toml"
    path.write_text('ocr = "no"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)
    path.write_text("ocr = false\n", encoding="utf-8")
    assert load_config(path).ocr is False
    assert Config().ocr is True


# --- agent + worker ------------------------------------------------------------------


def test_youtube_lecture_is_learning_from_the_saved_screenshot(cfg, store):
    agent, system, cap, worker, engine = make(cfg, store)
    system.fg = youtube("Striver DP 1")  # title alone says nothing
    for i in range(11):
        cap.shade = {1: 10 + 20 * i, 2: 200}  # monitor 1 changes every frame
        agent.tick(T0 + i)
    assert drain(worker) == 3  # every changed frame of the foreground monitor
    assert all(p.parent.parent.parent == cfg.shots_dir for p in engine.read_paths)
    assert all(p.name.endswith("_m1.jpg") for p in engine.read_paths)  # monitor 2 is not read
    [(title, cat, terms, goal)] = content_rows(store)
    assert (title, cat, goal) == ("Striver DP 1 - YouTube", "Learning", 0)
    assert "dynamic programming" in terms
    [seg] = segments(store)
    assert seg.category == "Learning" and seg.fit == "On-goal"


def test_vlog_stays_entertainment_and_unchanged_frames_are_not_read(cfg, store):
    agent, system, _cap, worker, engine = make(cfg, store, FakeEngine(VLOG_PAGE))
    system.fg = youtube("Goa trip")
    for i in range(16):
        agent.tick(T0 + i)
    assert drain(worker) == 1  # the frame never changed after the first one
    assert content_rows(store)[0][1] == "Entertainment"
    assert segments(store)[0].fit == "Distraction"


def test_other_monitor_window_is_read_on_its_own_monitor(cfg, store):
    agent, system, _cap, worker, engine = make(cfg, store)
    system.fg = youtube("Lecture", monitor_left=1920)
    agent.tick(T0)
    drain(worker)
    assert [p.name[-6:] for p in engine.read_paths] == ["m2.jpg"]


def test_goal_keyword_on_screen_makes_vague_code_on_goal(cfg, store):
    store.set_goal(DAY, T0, "DP practice", "memoization")
    agent, system, _cap, worker, _engine = make(cfg, store, FakeEngine("def solve(n):\n  # memoization table"))
    system.fg = Foreground("Code.exe", "main.py - scratch", (0, 0, 1920, 1080))
    agent.tick(T0)
    drain(worker)
    assert content_rows(store)[0][3] == 1
    [seg] = segments(store, ["memoization"])
    assert seg.category == "Tech/Work" and seg.fit == "On-goal"


def test_no_screenshot_or_excluded_windows_are_never_read(cfg, store):
    agent, system, cap, worker, engine = make(cfg, store)
    system.fg = Foreground("brave.exe", "Vanakkam DSA - Brave", (0, 0, 1920, 1080))
    agent.tick(T0)
    system.fg = Foreground("brave.exe", "ICICI net banking - Brave", (0, 0, 1920, 1080))
    agent.tick(T0 + 5)
    # A blocked window on the foreground monitor (not in front) blocks it too.
    system.fg = youtube("Lecture")
    system.others = [Foreground("brave.exe", "Razorpay checkout", (0, 0, 800, 600))]
    cap.shade[1] = 99
    agent.tick(T0 + 10)
    drain(worker)
    assert engine.read_paths == []


def test_screen_text_is_never_stored_or_logged(cfg, store, caplog):
    caplog.set_level(logging.DEBUG)
    agent, system, _cap, worker, _engine = make(cfg, store, FakeEngine(DSA_PAGE + "\n" + SECRET_LINE))
    system.fg = youtube("Lecture")
    agent.tick(T0)
    drain(worker)
    dump = "\n".join(store._conn.iterdump())
    assert "482913" not in dump and "take U forward" not in dump
    assert "482913" not in caplog.text and "subscribers" not in caplog.text


def test_delete_drops_queued_frames_and_skips_late_results(cfg, store):
    agent, system, _cap, worker, engine = make(cfg, store)
    system.fg = youtube("Lecture")
    agent.tick(T0)
    late = worker._queue.queue[0]
    agent.forget(T0 - 60, T0 + 60)
    assert worker._queue.empty()
    worker.process(late)  # a frame picked up before the delete
    assert engine.read_paths == []
    assert store.query("SELECT COUNT(*) FROM activity")[0][0] == 0


def test_deleted_screenshot_result_is_not_written(cfg, store):
    agent, system, _cap, worker, _engine = make(cfg, store)
    system.fg = youtube("Lecture")
    agent.tick(T0)
    store.mark_deleted([r[0] for r in store.query("SELECT id FROM screenshot")])  # e.g. weekly wipe
    drain(worker)
    assert content_rows(store)[0][1] is None


def test_weekly_wipe_before_the_read_is_skipped_quietly(cfg, store):
    agent, system, _cap, worker, engine = make(cfg, store)
    system.fg = youtube("Lecture")
    agent.tick(T0)
    assert retention.wipe_before(store, cfg.shots_dir, T0 + 1) == 2
    engine.fail = FileNotFoundError()
    drain(worker)
    assert worker.enabled and worker._failures == 0
    assert content_rows(store)[0][1] is None


def test_engine_errors_turn_ocr_off_after_many_in_a_row(cfg, store, monkeypatch):
    monkeypatch.setattr(ocr, "MAX_FAILURES", 3)
    agent, system, cap, worker, engine = make(cfg, store)
    engine.fail = RuntimeError("boom")
    system.fg = youtube("Lecture")
    for i in range(4):
        cap.shade[1] = 10 + 30 * i
        agent.tick(T0 + 5 * i)
        drain(worker)
    assert worker.enabled is False
    worker.submit(1, Path("x"), DAY)
    assert worker._queue.empty()


def test_full_queue_drops_new_frames_instead_of_blocking(cfg, store, monkeypatch):
    monkeypatch.setattr(ocr, "QUEUE_SIZE", 2)
    worker = OcrWorker(store, FakeEngine(), lambda: {})
    for i in range(5):
        worker.submit(i, Path("x"), DAY)
    assert worker._queue.qsize() == 2


def test_rules_file_change_reloads_word_lists(store):
    lists = {"Learning": ["alpha", "beta", "gamma"]}
    mtime = [1.0]
    clock = [0.0]
    worker = OcrWorker(store, FakeEngine(), lambda: lists, lambda: mtime[0], lambda: clock[0])
    assert worker._matcher.classify("alpha beta gamma")[0] == "Learning"
    lists = {"Other": ["alpha", "beta", "gamma"]}
    mtime[0] = 2.0
    clock[0] = 61.0
    worker._maybe_reload_rules()
    assert worker._matcher.classify("alpha beta gamma")[0] == "Other"


def test_make_engine_is_off_outside_windows(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    assert ocr.make_engine() is None


def test_replay_label_says_why_the_screen_text_resorted_it(cfg, store, tmp_path):
    from focus_tracker.dashboard import Dashboard

    agent, system, _cap, worker, _engine = make(cfg, store)
    system.fg = youtube("Striver DP 1")
    agent.tick(T0)
    drain(worker)
    dash = Dashboard(cfg, store, agent, rules_path=tmp_path / "rules.toml", clock=lambda: T0 + 3600)
    labels = dash.get("/api/replay", {})["labels"]
    assert labels[0].startswith("Striver DP 1 - YouTube")
    assert "screen text: Learning (" in labels[0] and "dynamic programming" in labels[0]


def test_review_queue_is_per_page_and_page_tags_only_fix_that_page(cfg, store, tmp_path):
    from focus_tracker.dashboard import ApiError, Dashboard

    agent = Agent(cfg, store, FakeSystem(), lambda: FakeCapturer(), None)
    dash = Dashboard(cfg, store, agent, rules_path=tmp_path / "rules.toml", clock=lambda: T0 + 3600)
    tricky = 'Mix "best" \\ songs \' 2026 - YouTube'
    for i, title in enumerate(["Striver graph series - YouTube", tricky]):
        aid = store.open_activity(T0 + i * 600, "brave.exe", title, 1, "https://www.youtube.com/watch")
        store.extend_activity(aid, T0 + i * 600 + 300)
    queue = dash.get("/api/settings", {})["queue"]
    assert {q["title"] for q in queue} == {"Striver graph series - YouTube", tricky}
    assert all(q["key"] == "www.youtube.com" and q["kind"] == "site" for q in queue)

    dash.post("/api/tag", {"kind": "site", "key": "www.youtube.com", "title": tricky,
                           "scope": "page", "category": "Entertainment"})
    rules = load_rules(tmp_path / "rules.toml")  # still valid TOML with quotes and backslashes
    assert categorize_with_content(rules, "brave.exe", tricky, "https://www.youtube.com/watch", "Learning") == "Entertainment"
    assert categorize_with_content(rules, "brave.exe", "Other video - YouTube", "https://www.youtube.com/watch", None) == "Other"
    assert [q["title"] for q in dash.get("/api/settings", {})["queue"]] == ["Striver graph series - YouTube"]

    for bad in ({"title": "x\x7f"}, {"title": "a\nb"}, {"title": ""}, {"title": "[excluded]"}, {"scope": "nope"}):
        body = {"kind": "site", "key": "www.youtube.com", "title": "ok", "scope": "page", "category": "Learning", **bad}
        with pytest.raises(ApiError):
            dash.post("/api/tag", body)
