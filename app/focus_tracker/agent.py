"""The collector loop: one tick per second.

Each tick decides the state (active / watching / idle / locked / paused), records
window switches (exact times from the Windows event hook, plus a 1 s poll as a
safety net), and every `screenshot_interval_s` saves one JPEG per monitor while
active or watching. Screens that barely changed are skipped.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import capture, plan, retention, review
from .browser import BrowserState, domain_matches, domain_of, sanitize_url
from .capture import Capturer
from .categorize import load_rules
from .config import Config
from .db import Store
from .metrics import LOCK_SCREEN_PROCESSES, Classifier
from .system import Foreground, System
from .titles import clean_title

log = logging.getLogger(__name__)

ACTIVE, WATCHING, IDLE, LOCKED, PAUSED = "active", "watching", "idle", "locked", "paused"
PRESENT = (ACTIVE, WATCHING)  # states where activity is tracked and screenshots taken
EXTENSION_SEEN_KEY = "extension_last_seen"
EXTENSION_TRUST_S = 7 * 86400  # private-window detection only while the extension is in use
EXCLUDED_TITLE = "[excluded]"
CAP_CHECK_EVERY_S = 600
CAPTURER_REFRESH_S = 600  # re-read the monitor layout (docking, plugging in)
CAPTURER_RETRY_S = 60
PLAN_CHECK_EVERY_S = 60


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

    @property
    def last(self) -> float | None:
        return self._last

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
        browser: BrowserState | None = None,
        break_prompter: Callable[[float], str | None] | None = None,
        ocr=None,  # ocr.OcrWorker: reads the saved screenshot files in the background
    ):
        self.cfg = cfg
        self.ocr = ocr
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
        self._capturer_created = 0.0
        self._capturer_failed_at = -CAPTURER_RETRY_S
        self._goal_lock = threading.Lock()
        self._goal_prompt_running = False
        self._goal_failed_day: str | None = None
        self._break_prompter = break_prompter
        self._away_since: float | None = None
        self._lock = threading.RLock()  # tick() vs forget() from the dashboard thread
        self._last_plan_check = 0.0
        self.browser = browser or BrowserState()
        seen = store.get_meta(EXTENSION_SEEN_KEY)
        self._extension_seen = float(seen) if seen else 0.0
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
        with self._lock:
            self._tick(now)

    def _tick(self, now: float) -> None:
        state = self._decide_state(now)
        self._track_break(now, state)
        self.current_state = state
        self.state.update(now, (state,))
        self._note_extension(now)

        switches = self.system.drain_switches()
        no_shot = True  # fail closed: no foreground window means no screenshot
        if state in PRESENT:
            for ts, win in switches:
                if win.process.lower() in LOCK_SCREEN_PROCESSES:
                    continue  # the lock screen is not activity; the next tick sees LOCKED
                # Exact switch times between ticks; never move a span backwards.
                self._record(min(max(ts, self.activity.last or ts), now), win)
            fg = self.system.foreground()
            no_shot = self._record(now, fg) if fg is not None else True
            if fg is None:
                self.activity.close()
            self._maybe_ask_goal(now)
        else:
            self.activity.close()

        if state in PRESENT and not no_shot and now - self._last_shot_ts >= self.cfg.screenshot_interval_s:
            self._last_shot_ts = now
            self._take_screenshots(now)

        if now - self._last_plan_check >= PLAN_CHECK_EVERY_S:
            self._last_plan_check = now
            self._maybe_plan(now)  # before the wipe, same weekly boundary
        retention.maybe_wipe(self.store, self.cfg, now)
        if now - self._last_cap_check >= CAP_CHECK_EVERY_S:
            self._last_cap_check = now
            retention.enforce_cap(self.store, self.cfg)

    def forget(self, lo: float, hi: float, day: str | None = None) -> int:
        """Delete everything recorded in [lo, hi): log rows and screenshot files.
        With `day` ([lo, hi) is that whole tracker day) also drop its goal, its
        screenshot rows and any stray files in its hour folders.
        Returns the number of screenshot files removed."""
        with self._lock:
            # Close the open spans so the next tick cannot stretch a row back over the gap.
            self.activity.close()
            self.state.close()
            if self.ocr is not None:
                self.ocr.drop_pending()
            shots = self.store.delete_range(lo, hi, day)
            removed = 0
            for _id, rel in shots:
                removed += self._unlink(self.cfg.shots_dir / rel)
            if day is not None:
                removed += self._sweep_hour_folders(lo, hi)
                if self._goal_failed_day == day:
                    self._goal_failed_day = None
            self._thumbs.clear()
            if self._away_since is not None and self._away_since >= lo:
                self._away_since = None
        log.info("Deleted %d screenshots and the log for a range of %.0f min", removed, (hi - lo) / 60)
        return removed

    @staticmethod
    def _unlink(path: Path) -> int:
        try:
            if path.is_file():
                path.unlink()
                return 1
        except OSError:
            log.warning("Could not delete a screenshot file", exc_info=True)
        return 0

    def _sweep_hour_folders(self, lo: float, hi: float) -> int:
        """Remove leftover screenshots (not in the database, e.g. after a crash) from the
        hour folders inside [lo, hi), then the folders themselves once empty. The day
        starts on a whole hour, so every hour folder in the range belongs to it."""
        removed = 0
        dates: set[Path] = set()
        ts = lo
        while ts < hi:
            folder = self.cfg.shots_dir / capture.shot_relpath(ts, 0).rsplit("/", 1)[0]
            ts += 3600
            if not folder.is_dir():
                continue
            for f in folder.glob("*.jpg"):
                removed += self._unlink(f)
            dates.add(folder.parent)
            try:
                folder.rmdir()
            except OSError:
                pass  # not empty (some other file) or in use: leave it
        for folder in dates:
            try:
                folder.rmdir()
            except OSError:
                pass  # still holds the other tracker day's hours
        return removed

    def _track_break(self, now: float, state: str) -> None:
        """After an idle or locked stretch of at least the idle threshold, ask once what it was."""
        if state in (IDLE, LOCKED):
            if self._away_since is None:
                # Idle is noticed only after the threshold: the break began at the last input.
                idle = self.system.idle_seconds() if state == IDLE else 0.0
                self._away_since = now - min(idle, self.cfg.idle_threshold_s)
            return
        start, self._away_since = self._away_since, None
        if (
            state == ACTIVE
            and start is not None
            and now - start >= self.cfg.idle_threshold_s
            and self.cfg.ask_after_break
            and self._break_prompter is not None
        ):
            threading.Thread(target=self._ask_break, args=(start, now), daemon=True, name="break").start()

    def _maybe_plan(self, now: float) -> None:
        try:
            made = plan.maybe_generate(self.store, lambda: Classifier(load_rules()), self.cfg, now)
        except Exception:
            log.exception("Weekly plan failed; retrying in an hour")
            self._last_plan_check = now + 3600 - PLAN_CHECK_EVERY_S
            return
        if made:
            log.info("Weekly plan saved for the week ending %s", made["end_day"])
            if self.cfg.ai_review:
                threading.Thread(target=self._write_review, args=(made,), daemon=True, name="review").start()

    def _write_review(self, made: dict) -> None:
        try:
            text = review.write_review(made, self.cfg.claude_path, self.cfg.claude_timeout_s)
            self.store.set_plan_review(made["end_day"], text)
        except Exception as exc:
            log.warning("AI review skipped: %s", exc)

    def _ask_break(self, start: float, end: float) -> None:
        try:
            label = self._break_prompter(end - start)
            if label:
                self.store.add_break_label(start, end, label)
        except Exception:
            log.exception("Break prompt failed")

    def _record(self, ts: float, win: Foreground) -> bool:
        """Log one foreground window at `ts`. Returns True if it must not be screenshotted."""
        url, private = self._browser_context(win, ts)
        domain = domain_of(url) if url else ""
        excluded = private or self._is_excluded(win) or domain_matches(domain, self.cfg.exclude_domains)
        no_shot = excluded or self._is_no_screenshot(win) or domain_matches(
            domain, self.cfg.no_screenshot_domains
        )
        title = EXCLUDED_TITLE if excluded else clean_title(win.title)
        capturer = self._capturer_or_none(ts)
        monitor = capturer.monitor_index_for_rect(win.monitor_rect) if capturer else None
        if capturer is not None and win.monitor_rect is not None and monitor is None:
            # The window is on a monitor mss does not know: the layout changed.
            self._reset_capturer()
        self.activity.update(ts, (win.process, title, monitor, None if excluded else url))
        return no_shot

    def _browser_context(self, win: Foreground, ts: float) -> tuple[str | None, bool]:
        """(sanitized url or None, looks like a private window)."""
        if win.process.lower() not in self.cfg.extension_browsers:
            return None, False
        tab = self.browser.tab_for_window(win.title)
        if tab is not None:
            return sanitize_url(tab.url, self.cfg.store_full_url) or None, False
        return None, self._is_private_browser(win, ts)

    def _is_private_browser(self, win: Foreground, ts: float) -> bool:
        """A Brave window the extension does not know about. The extension reports every
        normal window but never private ones; only trusted while the extension is in use."""
        if win.process.lower() not in self.cfg.extension_browsers:
            return False
        if ts - self._extension_seen > EXTENSION_TRUST_S:
            return False
        return not self.browser.is_known_window(win.title)

    def _note_extension(self, now: float) -> None:
        seen = self.browser.last_seen
        if seen and seen - self._extension_seen >= 3600:  # persist at most hourly
            self.store.set_meta(EXTENSION_SEEN_KEY, str(seen))
        if seen > self._extension_seen:
            self._extension_seen = seen

    def _decide_state(self, now: float) -> str:
        if now < self._paused_until:
            return PAUSED
        if self.system.is_locked() or self._lock_screen_in_front():
            return LOCKED
        if self.system.idle_seconds() >= self.cfg.idle_threshold_s:
            if self.cfg.detect_media and (self.browser.audible(now) or self.system.media_playing()):
                return WATCHING
            return IDLE
        return ACTIVE

    def _lock_screen_in_front(self) -> bool:
        try:
            fg = self.system.foreground()
        except Exception:
            return False
        return fg is not None and fg.process.lower() in LOCK_SCREEN_PROCESSES

    def _blocked_monitors(self, capturer: Capturer, now: float) -> set[int] | None:
        """Monitors showing any visible excluded / no-screenshot / private window, not
        just the foreground one. None means a blocked window's monitor is unknown: skip all."""
        blocked: set[int] = set()
        for win in self.system.visible_windows():
            if not (
                self._is_excluded(win)
                or self._is_no_screenshot(win)
                or self._is_private_browser(win, now)
            ):
                continue
            idx = capturer.monitor_index_for_rect(win.monitor_rect)
            if idx is None:
                return None
            blocked.add(idx)
        return blocked

    def _is_excluded(self, fg: Foreground) -> bool:
        if fg.process.lower() in self.cfg.exclude_processes:
            return True
        title = fg.title.lower()
        return any(k in title for k in self.cfg.exclude_title_keywords)

    def _is_no_screenshot(self, fg: Foreground) -> bool:
        title = fg.title.lower()
        return any(k in title for k in self.cfg.no_screenshot_title_keywords)

    def _capturer_or_none(self, now: float) -> Capturer | None:
        if self._capturer is not None and now - self._capturer_created >= CAPTURER_REFRESH_S:
            self._reset_capturer()
        if self._capturer is None:
            if now - self._capturer_failed_at < CAPTURER_RETRY_S:
                return None
            try:
                self._capturer = self._capturer_factory()
                self._capturer_created = now
            except Exception:
                log.exception("Could not start screen capture; retrying in %d s", CAPTURER_RETRY_S)
                self._capturer_failed_at = now
                return None
        return self._capturer

    def _reset_capturer(self) -> None:
        close = getattr(self._capturer, "close", None)
        if close:
            try:
                close()
            except Exception:
                log.debug("Closing capturer failed", exc_info=True)
        self._capturer = None
        self._thumbs.clear()

    def _take_screenshots(self, now: float) -> None:
        capturer = self._capturer_or_none(now)
        if capturer is None:
            return
        try:
            blocked = self._blocked_monitors(capturer, now)
        except Exception:
            log.exception("Could not list visible windows; skipping screenshot")
            return
        if blocked is None:
            return
        try:
            frames = capturer.grab()
        except Exception:
            log.exception("Screen grab failed")
            self._reset_capturer()  # recreate next time (e.g. display change)
            return
        for monitor, img in frames:
            if monitor in blocked:
                continue
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
            shot_id = self.store.add_screenshot(now, monitor, rel, size, self.activity.id)
            # Only the monitor showing the foreground window says what this stretch is.
            if self.ocr is not None and self.activity.key is not None and monitor == self.activity.key[2]:
                self.ocr.submit(shot_id, self.cfg.shots_dir / rel, self.day_key(now))

    def _maybe_ask_goal(self, now: float) -> None:
        if not self.cfg.ask_daily_goal or self._goal_prompter is None:
            return
        day = self.day_key(now)
        with self._goal_lock:
            if (
                self._goal_prompt_running
                or self._goal_failed_day == day
                or self.store.get_goal(day) is not None
            ):
                return
            self._goal_prompt_running = True
        threading.Thread(target=self._ask_goal, args=(day,), daemon=True, name="goal").start()

    def _ask_goal(self, day: str) -> None:
        try:
            answer = self._goal_prompter(day)
            text, keywords = answer if answer else (None, None)
            self.store.set_goal(day, time.time(), text or None, keywords or None)
        except Exception:
            log.exception("Goal prompt failed; not asking again today")
            self._goal_failed_day = day
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
        self._reset_capturer()
        log.info("Agent stopped")
