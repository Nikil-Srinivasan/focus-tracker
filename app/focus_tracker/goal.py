"""Small always-on-top prompts: today's goal (first active minute of the day) and
"what was that break?" after an idle or locked stretch."""

from __future__ import annotations

import threading

BREAK_LABELS = ("Thinking", "Break", "Call", "Distracted")
BREAK_PROMPT_TIMEOUT_MS = 120_000
_UI_LOCK = threading.Lock()  # one Tk window at a time (Tk is not happy with two threads)


def prompt_goal(day: str) -> tuple[str, str] | None:
    """Blocking. Returns (goal, keywords) or None if skipped/closed."""
    with _UI_LOCK:
        return _prompt_goal(day)


def _prompt_goal(day: str) -> tuple[str, str] | None:
    import tkinter as tk

    result: dict[str, tuple[str, str] | None] = {"value": None}
    root = tk.Tk()
    root.title(f"Focus tracker: goal for {day}")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    pad = {"padx": 12, "pady": 4}

    tk.Label(root, text="What will make today a win? (one line)", anchor="w").pack(fill="x", **pad)
    goal = tk.Entry(root, width=60)
    goal.pack(fill="x", **pad)
    tk.Label(
        root,
        text="Keywords, projects or sites that count as on-goal (comma separated)",
        anchor="w",
    ).pack(fill="x", **pad)
    keywords = tk.Entry(root, width=60)
    keywords.pack(fill="x", **pad)

    def save(_event=None) -> None:
        text = goal.get().strip()
        if text:
            result["value"] = (text, keywords.get().strip())
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=12, pady=(4, 12))
    tk.Button(buttons, text="Save", width=10, command=save).pack(side="right")
    tk.Button(buttons, text="Skip today", width=10, command=root.destroy).pack(side="right", padx=8)
    root.bind("<Return>", save)
    goal.focus_force()
    root.mainloop()
    return result["value"]


def prompt_break(away_s: float) -> str | None:
    """Blocking, one click. Returns one of BREAK_LABELS, or None if dismissed, timed
    out after 2 minutes, or another prompt is already open."""
    if not _UI_LOCK.acquire(blocking=False):
        return None
    try:
        return _prompt_break(away_s)
    finally:
        _UI_LOCK.release()


def _prompt_break(away_s: float) -> str | None:
    import tkinter as tk

    result: dict[str, str | None] = {"value": None}
    root = tk.Tk()
    root.title("Focus tracker")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    tk.Label(root, text=f"Welcome back. You were away {round(away_s / 60)} min. What was it?").pack(
        padx=12, pady=(12, 6)
    )
    row = tk.Frame(root)
    row.pack(padx=12, pady=(0, 12))

    def pick(label: str) -> None:
        result["value"] = label
        root.destroy()

    for label in BREAK_LABELS:
        tk.Button(row, text=label, width=11, command=lambda lb=label: pick(lb)).pack(side="left", padx=3)
    root.after(BREAK_PROMPT_TIMEOUT_MS, root.destroy)
    root.mainloop()
    return result["value"]
