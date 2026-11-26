"""Deterministic focus metrics computed from the activity and state logs.

Definitions follow the plan (thresholds are constants so weeks compare honestly):
- Deep block: 25+ min of On-goal time; a non-On-goal visit over 60 s, or a gap
  (idle, locked, paused) over 60 s, ends it. Study tools (notes, AI chat, web
  search) that match no goal keyword cannot start a block but keep one going for
  up to 15 min in a row; they count as On-goal inside any On-goal run, else Off-goal.
- Switches: app switches (process changes) and tab switches (title changes inside
  a browser), after dropping flickers under 2 s.
- Context switch: category change. Focus session: run of one category.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache

from .browser import domain_of
from .categorize import (  # noqa: F401  categorize is re-exported
    UNCATEGORIZED,
    Rule,
    categorize,
    categorize_with_content,
    goal_fit,
    parse_keywords,
)
from .db import Store
from .goal import BREAK_LABELS, REST_LABELS

DEEP_MIN_S = 25 * 60
MAX_INTERRUPT_S = 60
MAX_SUPPORT_S = 15 * 60
SUPPORT = "Support"
MAX_GAP_S = 60
FLICKER_S = 2
TRIGGER_WINDOW_S = 120
RABBIT_HOLE_S = 20 * 60
EXCLUDED_TITLE = "[excluded]"  # same marker the agent writes
PRIVATE = "Private"  # excluded windows: neither on-goal nor a distraction
BROWSERS = {"brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "vivaldi.exe"}
PRESENT_STATES = ("active", "watching")
# The Windows lock screen. It can be in front while the session is not (yet)
# reported locked, e.g. the curtain after waking from sleep: the agent counts it
# as locked, and older logs that recorded it are skipped here.
LOCK_SCREEN_PROCESSES = {"lockapp.exe"}
HIST_BINS = [(0, 1), (1, 5), (5, 15), (15, 25), (25, 45), (45, 90), (90, None)]  # minutes


@dataclass
class Seg:
    start: float
    end: float
    process: str
    title: str
    url: str
    category: str
    fit: str

    @property
    def dur(self) -> float:
        return self.end - self.start

    @property
    def label(self) -> str:
        """Short name for tables: site for browser tabs, app name otherwise."""
        if self.fit == PRIVATE:
            return "private / excluded"
        domain = domain_of(self.url) if self.url else ""
        if domain:
            return domain.removeprefix("www.")
        if self.process.lower() in BROWSERS and self.title:
            return self.title[:60]
        return self.process.removesuffix(".exe").removesuffix(".EXE")


@dataclass
class Block:
    start: float
    end: float
    segs: list[Seg] = field(default_factory=list)
    interrupted_by: str = ""

    @property
    def dur(self) -> float:
        return self.end - self.start


def day_bounds(day: date, rollover_hour: int) -> tuple[float, float]:
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=rollover_hour)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def day_of(ts: float, rollover_hour: int) -> date:
    return (datetime.fromtimestamp(ts) - timedelta(hours=rollover_hour)).date()


class Classifier:
    """Caches the category per distinct (process, title, url, screen-text verdict);
    rules are fixed per instance."""

    def __init__(self, rules: list[Rule]):
        self._cat = lru_cache(maxsize=50_000)(
            lambda p, t, u, c: categorize_with_content(rules, p, t, u, c)
        )

    def category(self, process: str, title: str, url: str, content_cat: str | None = None) -> str:
        return self._cat(process, title, url, content_cat or None)


def load_segments(
    store: Store, classifier: Classifier, lo: float, hi: float, keywords: list[str]
) -> list[Seg]:
    rows = store.query(
        "SELECT start_ts, end_ts, process, title, COALESCE(url, ''), content_cat, content_goal "
        "FROM activity WHERE end_ts >= ? AND start_ts < ? ORDER BY start_ts, id",
        (lo, hi),
    )
    out = []
    for start, end, process, title, url, content_cat, content_goal in rows:
        if process.lower() in LOCK_SCREEN_PROCESSES:
            continue
        start, end = max(start, lo), min(end, hi)
        if title == EXCLUDED_TITLE:
            out.append(Seg(start, end, process, title, url, PRIVATE, PRIVATE))
            continue
        cat = classifier.category(process, title, url, content_cat)
        fit = goal_fit(cat, process, title, url, keywords, bool(content_goal))
        out.append(Seg(start, end, process, title, url, cat, fit))
    return out


def present_spans(store: Store, lo: float, hi: float) -> list[tuple[float, float, str]]:
    rows = store.query(
        "SELECT start_ts, end_ts, state FROM state WHERE end_ts >= ? AND start_ts < ? ORDER BY start_ts",
        (lo, hi),
    )
    return [(max(s, lo), min(e, hi), st) for s, e, st in rows]


def deep_blocks(segs: list[Seg]) -> list[Block]:
    """Deep blocks, plus resolving each Support segment to On-goal (inside an On-goal
    run of any length) or Off-goal (outside one). Mutates seg.fit."""
    blocks: list[Block] = []
    cur: Block | None = None
    off_run = support_run = 0.0
    first_off: Seg | None = None
    prev_end: float | None = None

    def close(reason: str) -> None:
        nonlocal cur
        if cur is not None:
            cur.segs = [s for s in cur.segs if s.start < cur.end]
            for s in cur.segs:
                if s.fit == SUPPORT:
                    s.fit = "On-goal"
            if cur.dur >= DEEP_MIN_S:
                cur.interrupted_by = reason
                blocks.append(cur)
        cur = None

    for s in segs:
        if cur is not None and prev_end is not None and s.start - prev_end > MAX_GAP_S:
            close("away")
        if s.fit == "On-goal":
            if cur is None:
                cur = Block(s.start, s.end)
            cur.end = max(cur.end, s.end)
            off_run = support_run = 0.0
            first_off = None
        elif s.fit == SUPPORT and cur is not None:
            support_run += s.dur
            if support_run > MAX_SUPPORT_S:
                close(s.label)
            else:
                cur.end = max(cur.end, s.end)
                off_run, first_off = 0.0, None
        elif cur is not None:
            off_run += s.dur
            first_off = first_off or s
            if off_run > MAX_INTERRUPT_S:
                close(first_off.label)
                off_run, first_off = 0.0, None
        if cur is not None:
            cur.segs.append(s)
        else:
            off_run = support_run = 0.0
            first_off = None
        prev_end = s.end if prev_end is None else max(prev_end, s.end)
    close("still going")
    for s in segs:
        if s.fit == SUPPORT:
            s.fit = "Off-goal"
    return blocks


def _steady(segs: list[Seg]) -> list[Seg]:
    return [s for s in segs if s.dur >= FLICKER_S]


def switch_events(segs: list[Seg]) -> tuple[list[float], list[float]]:
    """(app switch times, tab switch times)."""
    apps, tabs = [], []
    steady = _steady(segs)
    for prev, s in zip(steady, steady[1:], strict=False):
        if s.start - prev.end > MAX_GAP_S:
            continue  # coming back from a break is not a switch
        if s.process != prev.process:
            apps.append(s.start)
        elif s.process.lower() in BROWSERS and (s.title, s.url) != (prev.title, prev.url):
            tabs.append(s.start)
    return apps, tabs


def runs(segs: list[Seg], key) -> list[tuple[str, float, float, list[Seg]]]:
    """Merge consecutive segments with the same key (gap <= MAX_GAP_S)."""
    out: list[tuple[str, float, float, list[Seg]]] = []
    for s in _steady(segs):
        k = key(s)
        if out and out[-1][0] == k and s.start - out[-1][2] <= MAX_GAP_S:
            last = out[-1]
            out[-1] = (k, last[1], max(last[2], s.end), last[3] + [s])
        else:
            out.append((k, s.start, s.end, [s]))
    return out


def _break_order(row: tuple) -> tuple[int, str]:
    label = row[0]
    return (BREAK_LABELS.index(label) if label in BREAK_LABELS else len(BREAK_LABELS), label)


def _median_min(values: list[float]) -> float | None:
    return round(statistics.median(values) / 60, 1) if values else None


def _top(counter: dict[str, float], n: int = 10) -> list[dict]:
    return [
        {"label": k, "minutes": round(v / 60, 1)}
        for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:n]
        if v >= 30
    ]


class DayReport:
    """All metrics for one tracker day (rollover hour to rollover hour)."""

    def __init__(self, store: Store, classifier: Classifier, day: date, rollover_hour: int, now: float):
        self.day = day
        self.lo, self.hi = day_bounds(day, rollover_hour)
        goal = store.get_goal(day.isoformat())
        self.goal_text = goal[0] if goal else None
        self.keywords = parse_keywords(goal[1] if goal else None)
        self.segs = load_segments(store, classifier, self.lo, self.hi, self.keywords)
        self.states = present_spans(store, self.lo, self.hi)
        self.blocks = deep_blocks(self.segs)
        self.now = now
        self.breaks = store.query(
            "SELECT label, COUNT(*), COALESCE(SUM(end_ts - start_ts), 0) FROM break_label "
            "WHERE start_ts >= ? AND start_ts < ? GROUP BY label",
            (self.lo, self.hi),
        )

    # basic totals
    def state_seconds(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for s, e, st in self.states:
            out[st] += max(0.0, e - s)
        return out

    def active_seconds(self) -> float:
        st = self.state_seconds()
        return sum(st.get(k, 0.0) for k in PRESENT_STATES)

    def by(self, attr: str) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for s in self.segs:
            out[getattr(s, attr)] += s.dur
        return out

    def first_present(self) -> float | None:
        starts = [s for s, _e, st in self.states if st in PRESENT_STATES]
        return min(starts) if starts else None

    def current_run(self) -> float | None:
        """Seconds of the On-goal run still going right now, if any."""
        if not self.segs or self.now - self.segs[-1].end > 90 or self.now >= self.hi:
            return None
        tail = self.blocks[-1] if self.blocks and self.blocks[-1].interrupted_by == "still going" else None
        if tail:
            return self.now - tail.start
        # A run shorter than a deep block: walk back over On-goal segments.
        start = None
        prev_start = self.segs[-1].end
        for s in reversed(self.segs):
            if s.fit != "On-goal" or prev_start - s.end > MAX_GAP_S:
                break
            start, prev_start = s.start, s.start
        return self.now - start if start is not None else None

    # pages
    def today(self) -> dict:
        active = self.active_seconds()
        hours = active / 3600 if active else 0
        apps, tabs = switch_events(self.segs)
        fit = self.by("fit")
        distraction = fit.get("Distraction", 0.0) + fit.get("Off-goal", 0.0)
        deep = sum(b.dur for b in self.blocks)
        first = self.first_present()
        ribbon = [
            {"category": k, "start": s, "end": e}
            for k, s, e, _ in runs(self.segs, lambda x: x.category)
        ]
        hourly_switches = Counter(datetime.fromtimestamp(t).hour for t in apps + tabs)
        return {
            "day": self.day.isoformat(),
            "goal": self.goal_text,
            "keywords": self.keywords,
            "bounds": [self.lo, self.hi],
            "kpis": {
                "active_min": round(active / 60),
                "watching_min": round(self.state_seconds().get("watching", 0) / 60),
                "deep_min": round(deep / 60),
                "deep_blocks": len(self.blocks),
                "longest_block_min": round(max((b.dur for b in self.blocks), default=0) / 60),
                "switches_per_hour": round((len(apps) + len(tabs)) / hours, 1) if hours else 0,
                "app_switches": len(apps),
                "tab_switches": len(tabs),
                "distraction_min": round(distraction / 60),
                "goal_fit_pct": round(100 * fit.get("On-goal", 0.0) / active) if active else 0,
                "current_run_min": round((self.current_run() or 0) / 60),
                "time_to_first_block_min": (
                    round((self.blocks[0].start - first) / 60) if self.blocks and first else None
                ),
            },
            "categories": _top(self.by("category"), 20),
            "fit": {k: round(v / 60, 1) for k, v in fit.items()},
            "ribbon": ribbon,
            "states": [
                {"state": st, "start": s, "end": e} for s, e, st in self.states if st not in PRESENT_STATES
            ],
            "hourly_switches": [hourly_switches.get(h, 0) for h in range(24)],
            "cards": self.cards(),
            "breaks": [
                {"label": label, "count": n, "minutes": round(secs / 60), "rest": label in REST_LABELS}
                for label, n, secs in sorted(self.breaks, key=_break_order)
            ],
            "rest_min": round(sum(secs for label, _n, secs in self.breaks if label in REST_LABELS) / 60),
        }

    def cards(self) -> list[dict]:
        out = []
        for b in self.blocks:
            topics: dict[str, float] = defaultdict(float)
            for s in b.segs:
                if s.fit == "On-goal":
                    topics[s.title or s.label] += s.dur
            top = sorted(topics.items(), key=lambda kv: -kv[1])[:3]
            out.append({
                "start": b.start,
                "end": b.end,
                "minutes": round(b.dur / 60),
                "topics": [t for t, _ in top],
                "interrupted_by": b.interrupted_by,
            })
        return out

    def focus(self) -> dict:
        sessions = [e - s for _k, s, e, _ in runs(self.segs, lambda x: x.category)]
        hist = []
        for lo_m, hi_m in HIST_BINS:
            n = sum(1 for d in sessions if d >= lo_m * 60 and (hi_m is None or d < hi_m * 60))
            hist.append({"bin": f"{lo_m}-{hi_m}" if hi_m else f"{lo_m}+", "count": n})
        gaps = [b.start - a.end for a, b in zip(self.blocks, self.blocks[1:], strict=False)]
        active_h = self.active_seconds() / 3600
        ctx = max(0, len(sessions) - 1)
        return {
            "histogram": hist,
            "median_session_min": _median_min(sessions),
            "context_switches_per_hour": round(ctx / active_h, 1) if active_h else 0,
            "recovery_median_min": _median_min(gaps),
            "blocks": self.cards(),
        }

    def distraction_episodes(self) -> list[tuple[str, float, float, list[Seg]]]:
        return [r for r in runs(self.segs, lambda x: x.fit) if r[0] == "Distraction"]

    def distractions(self) -> dict:
        sinks: dict[str, dict[str, float]] = {"Distraction": defaultdict(float), "Off-goal": defaultdict(float)}
        for s in self.segs:
            if s.fit in sinks:
                sinks[s.fit][s.label] += s.dur
        triggers: dict[tuple[str, str], list[float]] = defaultdict(list)
        steady = _steady(self.segs)
        for _k, start, end, segs in self.distraction_episodes():
            if end - start < MAX_INTERRUPT_S:
                continue
            before = [p for p in steady if p.end <= start + 1 and start - p.end <= TRIGGER_WINDOW_S
                      and p.fit != "Distraction"]
            if before:
                triggers[(before[-1].label, segs[0].label)].append(end - start)
        comm = [r for r in runs(self.segs, lambda x: x.category) if r[0] == "Communication"]
        active_h = self.active_seconds() / 3600
        holes = []
        fit_runs = runs(self.segs, lambda x: x.fit)
        for prev, r in zip(fit_runs, fit_runs[1:], strict=False):
            if r[0] == "Off-goal" and prev[0] == "On-goal" and r[2] - r[1] >= RABBIT_HOLE_S:
                sites = Counter()
                for s in r[3]:
                    sites[s.label] += s.dur
                holes.append({
                    "start": r[1], "minutes": round((r[2] - r[1]) / 60),
                    "from": prev[3][-1].label, "sites": [k for k, _ in sites.most_common(3)],
                })
        return {
            "distraction": _top(sinks["Distraction"]),
            "off_goal": _top(sinks["Off-goal"]),
            "triggers": [
                {"before": b, "went_to": t, "count": len(v), "minutes": round(sum(v) / 60)}
                for (b, t), v in sorted(triggers.items(), key=lambda kv: -sum(kv[1]))[:10]
            ],
            "communication": {
                "visits": len(comm),
                "per_hour": round(len(comm) / active_h, 1) if active_h else 0,
                "median_min": _median_min([e - s for _k, s, e, _ in comm]),
                "by_hour": [
                    sum(1 for _k, s, _e, _ in comm if datetime.fromtimestamp(s).hour == h) for h in range(24)
                ],
            },
            "rabbit_holes": holes,
        }


def heatmap(store: Store, classifier: Classifier, end_day: date, rollover_hour: int, days: int = 28) -> dict:
    """Deep-work minutes per clock hour for the last `days` tracker days."""
    rows = []
    for i in range(days - 1, -1, -1):
        d = end_day - timedelta(days=i)
        lo, hi = day_bounds(d, rollover_hour)
        goal = store.get_goal(d.isoformat())
        segs = load_segments(store, classifier, lo, hi, parse_keywords(goal[1] if goal else None))
        minutes = [0.0] * 24
        for b in deep_blocks(segs):
            t = b.start
            while t < b.end:
                dt = datetime.fromtimestamp(t)
                next_hour = (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
                step = min(next_hour, b.end) - t
                minutes[dt.hour] += step / 60
                t += step
        rows.append({"day": d.isoformat(), "minutes": [round(m) for m in minutes]})
    return {"rows": rows}


STILL_ON_S = 90  # last activity this recent on the current day = still at the PC


def day_edges(store: Store, end_day: date, rollover_hour: int, now: float, days: int = 14) -> dict:
    """First and last moment at the PC (any window in front while active or
    watching; the lock screen does not count) for each of the last `days` tracker
    days, plus the typical start and stop. The current day's stop is left out of
    the typical stop while it is still going."""
    rows = []
    starts, stops = [], []
    for i in range(days - 1, -1, -1):
        d = end_day - timedelta(days=i)
        lo, hi = day_bounds(d, rollover_hour)
        if lo > now:
            continue
        marks = ",".join("?" * len(LOCK_SCREEN_PROCESSES))
        first, last = store.query(
            "SELECT MIN(start_ts), MAX(end_ts) FROM activity "
            f"WHERE end_ts >= ? AND start_ts < ? AND LOWER(process) NOT IN ({marks})",
            (lo, hi, *sorted(LOCK_SCREEN_PROCESSES)),
        )[0]
        if first is None:
            rows.append({"day": d.isoformat(), "first": None, "last": None, "still_on": False,
                         "span_min": 0, "active_min": 0})
            continue
        first, last = max(first, lo), min(last, hi)
        still_on = lo <= now < hi and now - last <= STILL_ON_S
        active = sum(
            max(0.0, e - s) for s, e, st in present_spans(store, lo, hi) if st in PRESENT_STATES
        )
        rows.append({
            "day": d.isoformat(), "first": first, "last": last, "still_on": still_on,
            "span_min": round((last - first) / 60), "active_min": round(active / 60),
        })
        starts.append(first - lo)
        if not (lo <= now < hi):
            stops.append(last - lo)

    def typical(offsets: list[float]) -> dict | None:
        if not offsets:
            return None
        base = datetime.combine(end_day, datetime.min.time()) + timedelta(hours=rollover_hour)

        def fmt(off: float) -> str:
            return (base + timedelta(seconds=off)).strftime("%H:%M")

        return {"median": fmt(statistics.median(offsets)), "earliest": fmt(min(offsets)),
                "latest": fmt(max(offsets)), "days": len(offsets)}

    return {"rows": rows, "rollover_hour": rollover_hour, "start": typical(starts), "stop": typical(stops)}


def uncategorized(store: Store, classifier: Classifier, lo: float, hi: float, limit: int = 25) -> list[dict]:
    """Pages and apps the screen text and rules could not sort (Other), most time
    first: the review queue. One row per site or app + window title, because the
    same site can be work on one page and play on another."""
    totals: dict[tuple[str, str, str], float] = {}
    for s in load_segments(store, classifier, lo, hi, []):
        if s.category != UNCATEGORIZED:
            continue
        domain = domain_of(s.url) if s.url else ""
        key = ("site", domain, s.title) if domain else ("app", s.process.lower(), s.title)
        totals[key] = totals.get(key, 0.0) + s.dur
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
    return [
        {"kind": k[0], "key": k[1], "title": k[2][:300], "example": k[2][:80], "minutes": round(v / 60, 1)}
        for k, v in ranked
        if v >= 60
    ]
