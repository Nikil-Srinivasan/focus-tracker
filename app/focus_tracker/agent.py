"""The collector loop: one tick per second.

Each tick decides the state (active / idle / locked / paused), extends or opens
activity and state spans, and every `screenshot_interval_s` saves one JPEG per
monitor while active. Screens that barely changed are skipped.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from . import capture, retention
from .capture import Capturer
from .config import Config
from .db import Store
from .system import Foreground, System

log = logging.getLogger(__name__)

ACTIVE, IDLE, LOCKED, PAUSED = "active", "idle", "locked", "paused"
EXCLUDED_TITLE = "[excluded]"
CAP_CHECK_EVERY_S = 600


class Span:
    """A run of identical keys. Opens a new row when the key changes or after a gap
    (sleep, crash), otherwise extends the current row's end time."""

    def __init__(self, open_fn: Callable, extend_fn: Callable, max_gap_s: float):
        self._open, self._extend, self._max_gap = open_fn, extend_fn, max_gap_s
        self.key: tuple | None = None
        self.id: int | None = None
        self._last: float | None = None

    def update(self, now: float, key: tuple | None) -> int | None:
        if key is None:
            self.close()
            return None
        if (
            self.id is not None
            and key == self.key
            and self._last is not None
            and now - self._last <= self._max_gap
        ):
            self._extend(self.id, now)
        else:
            self.id = self._open(now, *key)
            self.key = key
        self._last = now
        return self.id

    def close(self) -> None:
        self.key, self.id, self._last = None, None, None


class Agent:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        system: System,
        capturer_factory: Callable[[], Capturer],
        goal_prompter: Callable[[str], tuple[str, str] | None] | None = None,
    ):
        self.cfg = cfg
        self.store = store
        self.system = system
        self._capturer_factory = capturer_factory
        self._capturer: Capturer | None = None
        self._goal_prompter = goal_prompter
        max_gap = max(10.0, cfg.window_poll_s * 5)
        self.activity = Span(store.open_activity, store.extend_activity, max_gap)
        self.state = Span(store.open_state, store.extend_state, max_gap)
        self._paused_until = 0.0
        self._last_shot_ts = 0.0
        self._last_cap_check = 0.0
        self._thumbs: dict[int, object] = {}
        self._goal_lock = threading.Lock()
        self._goal_prompt_running = False
        self.current_state = ACTIVE

    # controls (called from the tray thread)
    def pause(self, seconds: float, now: float | None = None) -> None:
        self._paused_until = (now or time.time()) + seconds

    def pause_until(self, ts: float) -> None:
        self._paused_until = ts

    def resume(self) -> None:
        self._paused_until = 0.0

    @property
    def paused_until(self) -> float:
        return self._paused_until

    def day_key(self, ts: float) -> str:
        dt = datetime.fromtimestamp(ts) - timedelta(hours=self.cfg.day_rollover_hour)
        return dt.date().isoformat()

    def next_day_start(self, ts: float) -> float:
        day = datetime.fromisoformat(self.day_key(ts))
        return (day + timedelta(days=1, hours=self.cfg.day_rollover_hour)).timestamp()

    # main step
    def tick(self, now: float) -> None:
        state = self._decide_state(now)
        self.current_state = state
        self.state.update(now, (state,))

        fg: Foreground | None = None
        excluded = False
        no_shot = False
        if state == ACTIVE:
            fg = self.system.foreground()
            if fg is not None:
                excluded = self._is_excluded(fg)
                no_shot = excluded or self._is_no_screenshot(fg)
                title = EXCLUDED_TITLE if excluded else fg.title
                capturer = self._capturer_or_none()
                monitor = capturer.monitor_index_for_rect(fg.monitor_rect) if capturer else None
                self.activity.update(now, (fg.process, title, monitor))
            else:
                self.activity.close()
            self._maybe_ask_goal(now)
        else:
            self.activity.close()

        if state == ACTIVE and not no_shot and now - self._last_shot_ts >= self.cfg.screenshot_interval_s:
            self._last_shot_ts = now
            self._take_screenshots(now)

        retention.maybe_wipe(self.store, self.cfg, now)
        if now - self._last_cap_check >= CAP_CHECK_EVERY_S:
            self._last_cap_check = now
            retention.enforce_cap(self.store, self.cfg)

    def _decide_state(self, now: float) -> str:
        if now < self._paused_until:
            return PAUSED
        if self.system.is_locked():
            return LOCKED
        if self.system.idle_seconds() >= self.cfg.idle_threshold_s:
            return IDLE
        return ACTIVE

    def _is_excluded(self, fg: Foreground) -> bool:
        if fg.process.lower() in self.cfg.exclude_processes:
            return True
        title = fg.title.lower()
        return any(k in title for k in self.cfg.exclude_title_keywords)

    def _is_no_screenshot(self, fg: Foreground) -> bool:
        title = fg.title.lower()
        return any(k in title for k in self.cfg.no_screenshot_title_keywords)

    def _capturer_or_none(self) -> Capturer | None:
        if self._capturer is None:
            try:
                self._capturer = self._capturer_factory()
            except Exception:
                log.exception("Could not start screen capture")
                return None
        return self._capturer

    def _take_screenshots(self, now: float) -> None:
        capturer = self._capturer_or_none()
        if capturer is None:
            return
        try:
            frames = capturer.grab()
        except Exception:
            log.exception("Screen grab failed")
            self._capturer = None  # recreate next time (e.g. display change)
            return
        for monitor, img in frames:
            thumb = capture.thumbnail(img)
            if not capture.is_changed(self._thumbs.get(monitor), thumb, self.cfg.dedupe_threshold):
                continue
            rel = capture.shot_relpath(now, monitor)
            try:
                size = capture.save_jpeg(img, self.cfg.shots_dir / rel, self.cfg.jpeg_quality)
            except OSError:
                log.exception("Could not save %s", rel)
                continue
            self._thumbs[monitor] = thumb
            self.store.add_screenshot(now, monitor, rel, size, self.activity.id)

    def _maybe_ask_goal(self, now: float) -> None:
        if not self.cfg.ask_daily_goal or self._goal_prompter is None:
            return
        day = self.day_key(now)
        with self._goal_lock:
            if self._goal_prompt_running or self.store.get_goal(day) is not None:
                return
            self._goal_prompt_running = True
        threading.Thread(target=self._ask_goal, args=(day,), daemon=True, name="goal").start()

    def _ask_goal(self, day: str) -> None:
        try:
            answer = self._goal_prompter(day)
            text, keywords = answer if answer else (None, None)
            self.store.set_goal(day, time.time(), text or None, keywords or None)
        except Exception:
            log.exception("Goal prompt failed")
        finally:
            with self._goal_lock:
                self._goal_prompt_running = False

    # lifecycle
    def run(self, stop: threading.Event) -> None:
        log.info("Agent started")
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.tick(time.time())
            except Exception:
                log.exception("Tick failed")
            stop.wait(max(0.0, self.cfg.window_poll_s - (time.monotonic() - started)))
        close = getattr(self._capturer, "close", None)
        if close:
            close()
        log.info("Agent stopped")
