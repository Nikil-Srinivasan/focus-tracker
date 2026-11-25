"""Study tools (notes, AI chat, web search) inside a learning loop count as deep work."""

from __future__ import annotations

from test_phase3 import DAY, LEETCODE, MIN, T0, YT_FUN, add, add_state, report

from focus_tracker.categorize import categorize, goal_fit, load_rules

LMS = ("brave.exe", "Start learning · Vanakkam DSA", "https://lms.vanakkamdsa.com/v2/courses/x/learn")
LMS_NO_URL = ("brave.exe", "Start learning · Vanakkam DSA - Brave", "")
SEARCH = ("brave.exe", "bfs grid islands - Google Search", "https://www.google.com/search")
SEARCH_NO_URL = ("brave.exe", "bfs grid islands - Google Search - Brave", "")
CRICKET = ("brave.exe", "ind vs aus score - Google Search", "https://www.google.com/search")
CLAUDE_WEB = ("brave.exe", "Debugging BFS visited set - Claude", "https://claude.ai/chat/abc")
CLAUDE_APP = ("claude.exe", "Claude", "")
ANYTYPE = ("Anytype.exe", "Anytype", "")
GOAL_KW = "leetcode, vanakkam"


def test_learning_loop_tools_are_study_tools(tmp_path):
    rules = load_rules(tmp_path / "none.toml")
    for what in (SEARCH, SEARCH_NO_URL, CLAUDE_WEB, CLAUDE_APP, ANYTYPE):
        assert categorize(rules, *what) == "Study tools", what
    for what in (LMS, LMS_NO_URL, LEETCODE):
        assert categorize(rules, *what) == "Learning", what
    # the Google home page, Gmail and YouTube are not study tools
    assert categorize(rules, "brave.exe", "Google", "https://www.google.com/") == "Other"
    assert categorize(rules, "brave.exe", "Inbox - Gmail", "https://mail.google.com/mail/u/0/") == "Communication"
    assert categorize(rules, *YT_FUN) == "Other"  # left to the screen text


def test_goal_fit_support_only_for_study_tools():
    kw = ["leetcode"]
    assert goal_fit("Study tools", *SEARCH, kw) == "Support"
    assert goal_fit("Study tools", "brave.exe", "leetcode 200 - Google Search", "", kw) == "On-goal"
    assert goal_fit("Learning", "brave.exe", "MDN", "https://developer.mozilla.org/", kw) == "Off-goal"
    assert goal_fit("Study tools", *SEARCH, []) == "On-goal"  # no goal: productive counts


def test_full_learning_loop_is_one_deep_block(store, tmp_path):
    store.set_goal(DAY.isoformat(), T0, "DSA graphs: lecture + 1 LeetCode", GOAL_KW)
    t = add(store, T0, 15, LMS)
    t = add(store, t, 4, ANYTYPE)
    t = add(store, t, 20, LEETCODE)
    t = add(store, t, 2, SEARCH)
    t = add(store, t, 8, CLAUDE_WEB)
    t = add(store, t, 0.5, YT_FUN)  # 30 s slip is still allowed
    t = add(store, t, 10, LEETCODE)
    t = add(store, t, 3, ANYTYPE)  # notes at the end still count
    add_state(store, T0, t)
    r = report(store, rules_path=tmp_path / "none.toml")
    assert len(r.blocks) == 1
    assert round(r.blocks[0].dur / MIN, 1) == 62.5
    assert r.blocks[0].interrupted_by == "still going"
    fit = r.today()["fit"]
    assert fit == {"On-goal": 62.0, "Distraction": 0.5}
    assert r.today()["kpis"]["goal_fit_pct"] == 99


def test_study_tools_alone_do_not_make_a_deep_block(store, tmp_path):
    store.set_goal(DAY.isoformat(), T0, "DSA", GOAL_KW)
    t = add(store, T0, 30, CRICKET)
    t = add(store, t, 30, CLAUDE_APP)
    add_state(store, T0, t)
    r = report(store, rules_path=tmp_path / "none.toml")
    assert r.blocks == []
    assert r.today()["fit"] == {"Off-goal": 60.0}


def test_long_study_tool_stretch_ends_the_block(store, tmp_path):
    store.set_goal(DAY.isoformat(), T0, "DSA", GOAL_KW)
    t = add(store, T0, 30, LEETCODE)
    t = add(store, t, 10, CRICKET)
    t = add(store, t, 10, CRICKET)  # 20 min in a row: over the 15 min allowance
    add_state(store, T0, t)
    r = report(store, rules_path=tmp_path / "none.toml")
    assert [round(b.dur / MIN) for b in r.blocks] == [40]
    assert r.blocks[0].interrupted_by == "google.com"
    assert r.today()["fit"] == {"On-goal": 40.0, "Off-goal": 10.0}


def test_short_run_with_study_tools_counts_on_goal(store, tmp_path):
    store.set_goal(DAY.isoformat(), T0, "DSA", GOAL_KW)
    t = add(store, T0, 10, LEETCODE)
    t = add(store, t, 5, CLAUDE_APP)
    t = add(store, t + 10 * MIN, 5, CLAUDE_APP)  # after a break, nothing on-goal yet
    r = report(store, rules_path=tmp_path / "none.toml")
    assert r.blocks == []
    assert r.today()["fit"] == {"On-goal": 15.0, "Off-goal": 5.0}
