"""System tray icon: status, pause, resume, open folder, quit."""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from datetime import datetime

from PIL import Image, ImageDraw

from .agent import PRESENT, Agent
from .timefmt import clock12

COLORS = {"active": (46, 160, 67), "paused": (219, 154, 4), "other": (130, 130, 130)}


def _icon(color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=color + (255,))
    return img


def run_tray(agent: Agent, stop: threading.Event, dashboard_url: str | None = None) -> None:
    """Blocking; returns when the user picks Quit or `stop` is set."""
    import pystray

    def status(_item) -> str:
        if agent.paused_until > time.time():
            until = datetime.fromtimestamp(agent.paused_until)
            return f"Paused until {until:%a} {clock12(until)}"
        return f"Status: {agent.current_state}"

    def pause(seconds: float):
        return lambda icon, item: (agent.pause(seconds), refresh(icon))

    def pause_tomorrow(icon, _item):
        agent.pause_until(agent.next_day_start(time.time()))
        refresh(icon)

    def resume(icon, _item):
        agent.resume()
        refresh(icon)

    def open_dashboard(_icon, _item):
        if dashboard_url:
            webbrowser.open(dashboard_url)

    def open_folder(_icon, _item):
        os.startfile(agent.cfg.data_dir)  # type: ignore[attr-defined]  # Windows only

    def quit_(icon, _item):
        stop.set()
        icon.stop()

    def refresh(icon) -> None:
        if agent.paused_until > time.time():
            key = "paused"
        else:
            key = "active" if agent.current_state in PRESENT else "other"
        icon.icon = _icon(COLORS[key])
        icon.update_menu()

    icon = pystray.Icon(
        "focus-tracker",
        _icon(COLORS["active"]),
        "Focus tracker",
        menu=pystray.Menu(
            pystray.MenuItem(status, None, enabled=False),
            pystray.MenuItem("Open dashboard", open_dashboard, default=True, enabled=bool(dashboard_url)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause 15 minutes", pause(15 * 60)),
            pystray.MenuItem("Pause 1 hour", pause(60 * 60)),
            pystray.MenuItem("Pause until tomorrow", pause_tomorrow),
            pystray.MenuItem("Resume", resume),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open data folder", open_folder),
            pystray.MenuItem("Quit", quit_),
        ),
    )

    def watcher() -> None:
        while not stop.wait(5):
            refresh(icon)
        icon.stop()

    threading.Thread(target=watcher, daemon=True, name="tray-refresh").start()
    icon.run()
