"""Weekly plan engine: fixed checks -> cost in hours -> score -> top 3 actions,
plus grading of last week's actions with the same metrics.

Everything here is local and deterministic. The optional Claude write-up only
narrates the numbers this module produces (see review.py).

Score = hours recoverable per week x consistency (share of tracked days showing
the pattern) / effort (1 = flip a setting, 2 = change a habit, 3 = change your
schedule or tools).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import metrics
from .db import Store
from .timefmt import hour12

MIN_DAY_ACTIVE_S = 30 * 60  # a day counts as tracked with 30+ min active
FULL_CONFIDENCE_DAYS = 5
BASELINE_WEEKS = 2
TARGET_STEP_H = 0.5
SLOW_START_MIN = 45
START_GOAL_MIN = 30
COMM_PER_HOUR_LIMIT = 2.0
PLATEAU_BAND = 0.1


@dataclass
class Target:
    metric: str  # one of METRICS
    key: str  # site/app label or "before -> went_to"; "" when not needed
    op: str  # "<=" or ">="
    value: float
    unit: str
    baseline: float  # the value this week, to judge "partial"


@dataclass
class Finding:
    check: str
    finding: str
    cost_h: float
    consistency: float
    effort: int
    action: str
    target: Target
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)


class Week:
    """The DayReports of 7 tracker days, plus the week-level numbers the checks need."""

    def __init__(self, store: Store, classifier: metrics.Classifier, end_day: date, rollover_hour: int,
                 now: float, refocus_min: float):
        self.end_day = end_day
        self.start_day = end_day - timedelta(days=6)
        self.refocus_s = refocus_min * 60
        self.days = [
            metrics.DayReport(store, classifier, self.start_day + timedelta(days=i), rollover_hour, now)
            for i in range(7)
        ]
        self.tracked = [d for d in self.days if d.active_seconds() >= MIN_DAY_ACTIVE_S]
        self.dist = {id(d): d.distractions() for d in self.tracked}  # computed once, used by several checks

    @property
    def n(self) -> int:
        return len(self.tracked)

    def active_h(self) -> float:
        return sum(d.active_seconds() for d in self.tracked) / 3600

    def deep_h_per_day(self) -> float:
        if not self.tracked:
            return 0.0
        return sum(b.dur for d in self.tracked for b in d.blocks) / 3600 / self.n

    # metrics shared by checks and grading ---------------------------------------
    def site_minutes(self, label: str) -> float:
        return sum(s.dur for d in self.tracked for s in d.segs
                   if s.label == label and s.fit in ("Distraction", "Off-goal")) / 60

    def episodes(self, label: str) -> int:
        """Visits to `label` that interrupted other work (distraction runs starting with it)."""
        return sum(1 for d in self.tracked for _k, _s, _e, segs in d.distraction_episodes()
                   if segs[0].label == label)

    def triggers(self) -> dict[tuple[str, str], list[float]]:
        """(before, went_to) -> [count, minutes lost] over the week."""
        out: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0])
        for d in self.tracked:
            for t in self.dist[id(d)]["triggers"]:
                entry = out[(t["before"], t["went_to"])]
                entry[0] += t["count"]
                entry[1] += t["minutes"]
        return out

    def trigger_count(self, key: str) -> float:
        before, _, went = key.partition(" -> ")
        return self.triggers().get((before, went), [0, 0.0])[0]

    def first_block_minutes(self) -> list[float | None]:
        out = []
        for d in self.tracked:
            first = d.first_present()
            out.append((d.blocks[0].start - first) / 60 if d.blocks and first else None)
        return out

    def fast_start_days(self, _key: str = "") -> float:
        return sum(1 for m in self.first_block_minutes() if m is not None and m <= START_GOAL_MIN)

    def comm_visits(self) -> tuple[int, Counter]:
        apps: Counter = Counter()
        total = 0
        for d in self.tracked:
            for k, _s, _e, segs in metrics.runs(d.segs, lambda x: x.category):
                if k == "Communication":
                    total += 1
                    apps[segs[0].label] += 1
        return total, apps

    def comm_per_hour(self, _key: str = "") -> float:
        h = self.active_h()
        return round(self.comm_visits()[0] / h, 2) if h else 0.0

    def rabbit_minutes(self, _key: str = "") -> float:
        return sum(r["minutes"] for d in self.tracked for r in self.dist[id(d)]["rabbit_holes"])

    def late_split(self) -> tuple[float, float, int]:
        """(distraction min in first halves, in second halves, days where the second half is worse)."""
        first = second = 0.0
        worse = 0
        for d in self.tracked:
            present = [s for s in d.segs if s.fit != metrics.PRIVATE]
            if not present:
                continue
            mid = present[0].start + (present[-1].end - present[0].start) / 2
            a = sum(s.dur for s in present if s.fit == "Distraction" and s.start < mid) / 60
            b = sum(s.dur for s in present if s.fit == "Distraction" and s.start >= mid) / 60
            first, second = first + a, second + b
            if b > max(1.5 * a, a + 10):
                worse += 1
        return first, second, worse

    def late_excess_minutes(self, _key: str = "") -> float:
        a, b, _ = self.late_split()
        return round(max(0.0, b - a), 1)

    def offgoal_minutes(self, _key: str = "") -> float:
        return sum(s.dur for d in self.tracked for s in d.segs if s.fit == "Off-goal") / 60

    def days_with(self, pred) -> int:
        return sum(1 for d in self.tracked if pred(d))


METRICS = {
    "site_minutes": lambda w, k: w.site_minutes(k),
    "trigger_count": lambda w, k: w.trigger_count(k),
    "fast_start_days": lambda w, k: w.fast_start_days(k),
    "comm_per_hour": lambda w, k: w.comm_per_hour(k),
    "rabbit_minutes": lambda w, k: w.rabbit_minutes(k),
    "late_excess_minutes": lambda w, k: w.late_excess_minutes(k),
    "offgoal_minutes": lambda w, k: w.offgoal_minutes(k),
}


def _h(minutes: float) -> str:
    m = round(minutes)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


# checks ---------------------------------------------------------------------------------

def check_top_sink(w: Week) -> Finding | None:
    totals: dict[str, float] = defaultdict(float)
    kinds: dict[str, bool] = {}
    for d in w.tracked:
        for s in d.segs:
            if s.fit == "Distraction":
                totals[s.label] += s.dur / 60
                kinds[s.label] = bool(s.url)
    if not totals:
        return None
    label, minutes = max(totals.items(), key=lambda kv: kv[1])
    if minutes < 20:
        return None
    visits = w.episodes(label)
    days = w.days_with(lambda d: any(s.label == label and s.fit == "Distraction" for s in d.segs))
    is_site = kinds.get(label, False)
    return Finding(
        check="top_sink",
        finding=f"{label} took {_h(minutes)} across {days} of {w.n} tracked days, with {visits} visits that broke into other work.",
        cost_h=(minutes + visits * w.refocus_s / 60) / 60,
        consistency=days / w.n,
        effort=1 if is_site else 2,
        action=(f"Block {label} in Brave during your working hours (a site blocker), or allow it only after your first deep block."
                if is_site else f"Close {label} while you work and open it only after a deep block."),
        target=Target("site_minutes", label, "<=", round(minutes / 2), "min", round(minutes)),
    )


def check_trigger(w: Week) -> Finding | None:
    trig = w.triggers()
    if not trig:
        return None
    (before, went), (count, lost) = max(trig.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    if count < 3:
        return None
    days = w.days_with(lambda d: any(t["before"] == before and t["went_to"] == went
                                     for t in w.dist[id(d)]["triggers"]))
    return Finding(
        check="trigger",
        finding=f"{count} times you went from {before} straight to {went}, losing {_h(lost)}.",
        cost_h=(lost + count * w.refocus_s / 60) / 60,
        consistency=days / w.n,
        effort=2,
        action=f"When you feel the pull to {went} while on {before}, write your next step in a note first, then decide.",
        target=Target("trigger_count", f"{before} -> {went}", "<=", count // 2, "times", count),
    )


def check_slow_start(w: Week) -> Finding | None:
    starts = w.first_block_minutes()
    slow = [m for m in starts if m is None or m > SLOW_START_MIN]
    if len(slow) < max(2, w.n / 2):
        return None
    known = [m for m in starts if m is not None]
    median = statistics.median(known) if known else None
    # Cost: minutes past the 30-min goal on slow days (days with no block at all count as 60).
    cost_min = sum((m if m is not None else START_GOAL_MIN + 60) - START_GOAL_MIN for m in slow)
    fast = w.fast_start_days()
    text = (f"Your first deep block started a median {_h(median)} after you sat down; slow or missing on {len(slow)} of {w.n} days."
            if median is not None else f"No deep block at all on {len(slow)} of {w.n} days.")
    return Finding(
        check="slow_start",
        finding=text,
        cost_h=cost_min / 60,
        consistency=len(slow) / w.n,
        effort=2,
        action="First 25 minutes after unlock: one task picked the night before from your goal. No browser, no chat.",
        target=Target("fast_start_days", "", ">=", min(w.n, int(fast) + max(1, w.n // 2)), "days", fast),
    )


def check_comm_pull(w: Week) -> Finding | None:
    total, apps = w.comm_visits()
    per_hour = w.comm_per_hour()
    if per_hour <= COMM_PER_HOUR_LIMIT or total < 5:
        return None
    top = ", ".join(a for a, _ in apps.most_common(2))
    days = w.days_with(lambda d: any(s.category == "Communication" for s in d.segs))
    target = max(COMM_PER_HOUR_LIMIT, round(per_hour / 2, 1))
    excess = total - target * w.active_h()
    return Finding(
        check="comm_pull",
        finding=f"Chat and mail ({top}) pulled you in {per_hour:.1f} times per active hour, {total} times this week.",
        cost_h=max(0.0, excess) * w.refocus_s / 3600,
        consistency=days / w.n,
        effort=1,
        action=f"Turn off notifications for {top}; check them at :00 and :30 only.",
        target=Target("comm_per_hour", "", "<=", target, "per hour", per_hour),
    )


def check_rabbit_holes(w: Week) -> Finding | None:
    holes = [r for d in w.tracked for r in w.dist[id(d)]["rabbit_holes"]]
    if not holes:
        return None
    minutes = sum(r["minutes"] for r in holes)
    sites = Counter(s for r in holes for s in r["sites"]).most_common(2)
    days = w.days_with(lambda d: bool(w.dist[id(d)]["rabbit_holes"]))
    return Finding(
        check="rabbit_holes",
        finding=f"{len(holes)} rabbit holes ({_h(minutes)}) drifted from your goal into {', '.join(s for s, _ in sites)}.",
        cost_h=minutes / 60,
        consistency=days / w.n,
        effort=2,
        action="When a side topic grabs you, add it to a 'later' list and go back; explore it only after the goal is done, time-boxed to 15 min.",
        target=Target("rabbit_minutes", "", "<=", round(minutes / 2), "min", minutes),
    )


def check_late_collapse(w: Week) -> Finding | None:
    a, b, worse = w.late_split()
    if worse < max(2, w.n / 2) or b - a < 30:
        return None
    return Finding(
        check="late_collapse",
        finding=f"Distraction in the second half of your day was {_h(b)} against {_h(a)} in the first half; worse on {worse} of {w.n} days.",
        cost_h=(b - a) / 60,
        consistency=worse / w.n,
        effort=3,
        action="Put a real 20-minute break (walk, no screen) at the middle of your day, and end the day when focus is gone instead of drifting.",
        target=Target("late_excess_minutes", "", "<=", round((b - a) / 2), "min", round(b - a, 1)),
    )


def check_off_goal(w: Week) -> Finding | None:
    minutes = w.offgoal_minutes()
    if minutes < 60:
        return None
    days = w.days_with(lambda d: any(s.fit == "Off-goal" for s in d.segs))
    return Finding(
        check="off_goal",
        finding=f"{_h(minutes)} of tech or learning did not match the day's goal keywords.",
        cost_h=minutes / 60 / 2,  # half of it is often useful; count only half as recoverable
        consistency=days / w.n,
        effort=2,
        action="Write tomorrow's goal and keywords the evening before, and keep side learning until the goal is done.",
        target=Target("offgoal_minutes", "", "<=", round(minutes / 2), "min", round(minutes)),
    )


CHECKS = [check_top_sink, check_trigger, check_slow_start, check_comm_pull,
          check_rabbit_holes, check_late_collapse, check_off_goal]


# grading and the plan -------------------------------------------------------------------

def grade(prev_actions: list[dict], w: Week) -> list[dict]:
    out = []
    for a in prev_actions:
        t = a["target"]
        now = METRICS[t["metric"]](w, t["key"])
        now = round(now) if t["unit"] == "min" else round(now, 1)
        if t["op"] == "<=":
            hit = now <= t["value"]
            progress = (t["baseline"] - now) / max(1e-9, t["baseline"] - t["value"])
        else:
            hit = now >= t["value"]
            progress = (now - t["baseline"]) / max(1e-9, t["value"] - t["baseline"])
        result = "hit" if hit else "partial" if progress >= 0.5 else "missed"
        out.append({"check": a["check"], "action": a["action"], "target": t, "actual": now, "result": result})
    return out


def deep_target(history: list[dict], this_week_h: float, cap_h: float) -> dict:
    """Baseline for the first 2 weeks, then last week's average + 30 min, capped."""
    weeks = [h["deep_h_per_day"] for h in history] + [this_week_h]
    if len(weeks) < BASELINE_WEEKS:
        return {"target_h": None, "note": f"Measuring your baseline (week {len(weeks)} of {BASELINE_WEEKS})."}
    target = min(cap_h, round(this_week_h + TARGET_STEP_H, 1))
    note = f"Last week averaged {this_week_h:.1f} h/day, so next week's bar is {target:.1f} h/day (goal {cap_h:g} h)."
    last3 = weeks[-3:]
    if len(last3) == 3 and max(last3) - min(last3) <= PLATEAU_BAND * max(max(last3), 0.1):
        note += " You have plateaued for 3 weeks: change the schedule, not the effort."
    return {"target_h": target, "note": note}


def best_hours(store: Store, classifier: metrics.Classifier, end_day: date, rollover_hour: int) -> str | None:
    rows = metrics.heatmap(store, classifier, end_day, rollover_hour)["rows"]
    totals = [sum(r["minutes"][h] for r in rows) for h in range(24)]
    if sum(totals) < 60:
        return None
    start = max(range(23), key=lambda h: totals[h] + totals[h + 1])
    return f"{hour12(start)} to {hour12(start + 2)}"


def build_plan(store: Store, classifier: metrics.Classifier, end_day: date, rollover_hour: int, now: float,
               refocus_min: float = 10, deep_goal_h: float = 8) -> dict:
    w = Week(store, classifier, end_day, rollover_hour, now, refocus_min)
    history = previous_plans(store, end_day)
    prev = history[-1] if history and history[-1]["end_day"] == (end_day - timedelta(days=7)).isoformat() else None
    graded = grade(prev["actions"], w) if prev else []
    # Missed twice in a row: the action was wrong, so skip that check this week.
    banned = set()
    if prev and len(history) >= 2:
        before_prev = {g["check"]: g["result"] for g in prev.get("graded", [])}
        banned = {g["check"] for g in graded if g["result"] == "missed" and before_prev.get(g["check"]) == "missed"}
    findings: list[Finding] = []
    if w.n:
        for check in CHECKS:
            f = check(w)
            if f is None or f.check in banned:
                continue
            f.cost_h = round(f.cost_h, 1)
            f.consistency = round(f.consistency, 2)
            f.score = round(f.cost_h * f.consistency / f.effort, 2)
            findings.append(f)
    findings.sort(key=lambda f: -f.score)
    deep = round(w.deep_h_per_day(), 2)
    confidence = "ok" if w.n >= FULL_CONFIDENCE_DAYS else "low"
    return {
        "end_day": end_day.isoformat(),
        "start_day": w.start_day.isoformat(),
        "created": now,
        "tracked_days": w.n,
        "confidence": confidence,
        "confidence_note": (
            f"Based on {w.n} tracked day{'s' if w.n != 1 else ''}. Patterns firm up after {FULL_CONFIDENCE_DAYS}+ days a week."
            if confidence == "low" else f"Based on {w.n} tracked days."
        ),
        "active_h": round(w.active_h(), 1),
        "deep_h_per_day": deep,
        "deep": deep_target(history, deep, deep_goal_h),
        "best_hours": best_hours(store, classifier, end_day, rollover_hour),
        "actions": [asdict(f) for f in findings[:3]],
        "other_findings": [asdict(f) for f in findings[3:]],
        "graded": graded,
        "skipped_checks": sorted(banned),
    }


def previous_plans(store: Store, before_day: date) -> list[dict]:
    rows = store.query("SELECT data FROM plan WHERE end_day < ? ORDER BY end_day", (before_day.isoformat(),))
    return [json.loads(r[0]) for r in rows]


def week_end_for(boundary_ts: float, rollover_hour: int) -> date:
    """The tracker day the weekly boundary falls in (Sunday for the default schedule)."""
    return metrics.day_of(boundary_ts, rollover_hour)


def maybe_generate(store: Store, classifier_factory, cfg, now: float) -> dict | None:
    """Store the plan for the week that ended at the latest weekly boundary, once."""
    from .retention import SCHEDULE_CHANGED_KEY, last_wipe_boundary

    hh, mm = cfg.wipe_hour_minute
    boundary = last_wipe_boundary(datetime.fromtimestamp(now), cfg.wipe_weekday, hh, mm)
    changed = store.get_meta(SCHEDULE_CHANGED_KEY)
    if changed is not None and boundary.timestamp() <= float(changed):
        return None  # the schedule moved after this boundary: start with the next one
    end_day = week_end_for(boundary.timestamp() - 1, cfg.day_rollover_hour)
    if store.query("SELECT 1 FROM plan WHERE end_day = ?", (end_day.isoformat(),)):
        return None
    # Skip weeks from before the tracker existed (first run on a fresh install).
    first = store.query("SELECT MIN(start_ts) FROM activity")[0][0]
    if first is None or metrics.day_of(first, cfg.day_rollover_hour) > end_day:
        return None
    plan = build_plan(store, classifier_factory(), end_day, cfg.day_rollover_hour, now,
                      cfg.refocus_penalty_min, cfg.deep_work_goal_h)
    store.save_plan(end_day.isoformat(), now, json.dumps(plan))
    return plan
