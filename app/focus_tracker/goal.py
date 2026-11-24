"""Small always-on-top window asking for today's goal (first active minute of the day)."""

from __future__ import annotations


def prompt_goal(day: str) -> tuple[str, str] | None:
    """Blocking. Returns (goal, keywords) or None if skipped/closed."""
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
