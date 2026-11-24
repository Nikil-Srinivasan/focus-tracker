"""Quick check of what the collector recorded: `python -m focus_tracker.stats`.

Prints today's active/idle/locked time, app switches, top apps and screenshot
storage, so you can verify the storage estimates on your own machine.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from .browser import domain_of
from .categorize import categorize, goal_fit, load_rules, parse_keywords
from .config import load_config
from .db import Store


def _fmt(seconds: float) -> str:
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h {m:02d}m"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-ago", type=int, default=0)
    args = parser.parse_args()
    cfg = load_config()
    store = Store(cfg.db_path)

    now = datetime.now() - timedelta(hours=cfg.day_rollover_hour)
    day = (now - timedelta(days=args.days_ago)).date()
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=cfg.day_rollover_hour)
    lo, hi = start.timestamp(), (start + timedelta(days=1)).timestamp()

    print(f"Day {day} (starts {cfg.day_rollover_hour:02d}:00)")
    for state, secs in store.query(
        "SELECT state, SUM(end_ts - start_ts) FROM state WHERE start_ts >= ? AND start_ts < ? "
        "GROUP BY state ORDER BY 2 DESC",
        (lo, hi),
    ):
        print(f"  {state:<8} {_fmt(secs)}")

    switches = store.query(
        "SELECT COUNT(*) FROM activity WHERE start_ts >= ? AND start_ts < ?", (lo, hi)
    )[0][0]
    print(f"  window/app changes: {switches}")
    print("  top apps:")
    for process, secs in store.query(
        "SELECT process, SUM(end_ts - start_ts) FROM activity WHERE start_ts >= ? AND start_ts < ? "
        "GROUP BY process ORDER BY 2 DESC LIMIT 8",
        (lo, hi),
    ):
        print(f"    {process:<28} {_fmt(secs)}")

    goal = store.get_goal(day.isoformat())
    keywords = parse_keywords(goal[1] if goal else None)
    rules = load_rules()
    by_cat: dict[str, float] = {}
    by_fit: dict[str, float] = {}
    by_domain: dict[str, float] = {}
    for process, title, url, secs in store.query(
        "SELECT process, title, COALESCE(url, ''), end_ts - start_ts FROM activity "
        "WHERE start_ts >= ? AND start_ts < ?",
        (lo, hi),
    ):
        cat = categorize(rules, process, title, url)
        by_cat[cat] = by_cat.get(cat, 0) + secs
        fit = goal_fit(cat, process, title, url, keywords)
        by_fit[fit] = by_fit.get(fit, 0) + secs
        if url:
            d = domain_of(url)
            by_domain[d] = by_domain.get(d, 0) + secs
    print("  categories:")
    for cat, secs in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"    {cat:<28} {_fmt(secs)}")
    print("  goal fit:  " + ", ".join(f"{k} {_fmt(v)}" for k, v in sorted(by_fit.items())))
    if by_domain:
        print("  top sites:")
        for d, secs in sorted(by_domain.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {d:<28} {_fmt(secs)}")

    count, size = store.query(
        "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM screenshot WHERE ts >= ? AND ts < ?", (lo, hi)
    )[0]
    avg = size / count / 1024 if count else 0
    print(f"  screenshots: {count} files, {size / 1024**2:.0f} MB, avg {avg:.0f} KB")
    print(f"  all live screenshots: {store.live_screenshot_bytes() / 1024**3:.2f} GB "
          f"(cap {cfg.max_storage_gb} GB, wiped weekly)")
    print(f"  goal: {goal[0] if goal and goal[0] else '(none)'}")
    store.close()


if __name__ == "__main__":
    main()
