from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from focus_tracker.config import Config
from focus_tracker.db import Store
from focus_tracker.system import Foreground


class FakeSystem:
    def __init__(self) -> None:
        self.fg: Foreground | None = Foreground("Code.exe", "agent.py - focus-tracker", (0, 0, 1920, 1080))
        self.idle = 0.0
        self.locked = False
        self.others: list[Foreground] = []  # visible windows besides the foreground one

    def foreground(self):
        return self.fg

    def idle_seconds(self):
        return self.idle

    def is_locked(self):
        return self.locked

    def visible_windows(self):
        return ([self.fg] if self.fg else []) + self.others


class FakeCapturer:
    """Two 1080p-shaped monitors (small images for speed). Change `shade` to alter frames."""

    def __init__(self) -> None:
        self.shade = {1: 10, 2: 200}
        self.grabs = 0
        self.closed = False

    def close(self):
        self.closed = True

    def grab(self):
        self.grabs += 1
        return [(m, Image.new("RGB", (192, 108), (s, s, s))) for m, s in self.shade.items()]

    def monitor_index_for_rect(self, rect):
        if rect is None:
            return None
        return 1 if rect[0] == 0 else 2


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(data_dir=tmp_path / "data")
    c.validate()
    return c


@pytest.fixture
def store(cfg: Config):
    s = Store(cfg.db_path)
    yield s
    s.close()
