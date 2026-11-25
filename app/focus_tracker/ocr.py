"""Read text from saved screenshots (local OCR) to tell what a stretch really was.

Every app and site is sorted this way first: the same app can be work or play.
Your own rules (rules.toml) still win, and the built-in rules are only a fallback
when the screen text decided nothing; with neither, a stretch is Other.

Only JPEG files the agent already saved are read, so anything that is never
screenshotted (excluded, no-screenshot, private, idle, locked, paused) is never
read. The agent queues one file per changed frame, for the monitor showing the
foreground window; a background thread reads it with the OCR engine built into
Windows (Windows.Media.Ocr: offline, no GPU).

The text itself is never stored, logged or sent anywhere. Each frame is reduced
to a verdict (a category from the [[content]] word lists, the words that matched,
and whether a daily-goal keyword was on screen), and the activity row it belongs
to keeps the majority verdict of its frames. Deleting a day deletes those rows.
"""

from __future__ import annotations

import logging
import queue
import re
import sys
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image

from .categorize import PRODUCTIVE, parse_keywords
from .db import Store

log = logging.getLogger(__name__)

MIN_HITS = 3  # different words of one list on one screenshot
MIN_LEAD = 2  # ... and this many more than any other list
MAX_TERMS = 8  # matched words kept per activity row (shown as the reason)
QUEUE_SIZE = 32  # frames waiting; newer frames are dropped when full
TRACKED_ACTIVITIES = 64  # per-row vote tallies kept in memory
RULES_RECHECK_S = 60.0
MAX_FAILURES = 20  # consecutive engine errors before OCR turns itself off


class Engine(Protocol):
    def read(self, path: Path) -> str: ...


def _term_pattern(terms: list[str]) -> re.Pattern | None:
    """Whole words or phrases, longest first so 'crash course' is one hit, plural 's' allowed."""
    terms = sorted({t for t in terms if t}, key=len, reverse=True)
    if not terms:
        return None
    alts = "|".join(r"\s+".join(re.escape(p) for p in t.split()) for t in terms)
    return re.compile(rf"(?<!\w)(?:{alts})s?(?!\w)", re.IGNORECASE)


def _norm(match: str) -> str:
    return " ".join(match.lower().split())


class ContentMatcher:
    """Scores screen text against the [[content]] word lists."""

    def __init__(self, lists: dict[str, list[str]]):
        self._patterns = {cat: p for cat, words in lists.items() if (p := _term_pattern(words))}
        self._known = {cat: {_norm(w) for w in words} for cat, words in lists.items()}

    def _hits(self, cat: str, text: str) -> set[str]:
        found = set()
        for m in self._patterns[cat].finditer(text):
            term = _norm(m.group(0))
            if term not in self._known[cat] and term.endswith("s"):
                term = term[:-1]  # "arrays" counts as "array"
            found.add(term)
        return found

    def classify(self, text: str) -> tuple[str | None, list[str]]:
        """(category or None if undecided, the words that matched for it).

        The best list needs MIN_HITS different words and a lead of MIN_LEAD over the
        best list on the other side (productive vs not). Two productive lists (say a
        coding tutorial: Learning and Tech/Work words) don't cancel out: the bigger
        count wins, ties go to the list that comes first."""
        scores = [(cat, self._hits(cat, text)) for cat in self._patterns]
        if not scores:
            return None, []
        best, words = max(scores, key=lambda kv: len(kv[1]))  # first of equals wins
        side = best in PRODUCTIVE
        other = max((len(w) for cat, w in scores if (cat in PRODUCTIVE) != side), default=0)
        if len(words) >= MIN_HITS and len(words) - other >= MIN_LEAD:
            return best, sorted(words)
        return None, []


def goal_on_screen(text: str, keywords: list[str]) -> bool:
    """Whole-word match of any daily-goal keyword (substrings would make 'dp' match 'dpi')."""
    pattern = _term_pattern(keywords)
    return bool(pattern and pattern.search(text))


@dataclass
class _Tally:
    frames: int = 0
    goal_frames: int = 0
    votes: Counter = field(default_factory=Counter)
    terms: dict[str, Counter] = field(default_factory=dict)  # per category

    def add(self, category: str | None, terms: list[str], goal: bool) -> None:
        self.frames += 1
        self.goal_frames += goal
        if category:
            self.votes[category] += 1
            self.terms.setdefault(category, Counter()).update(terms)

    def verdict(self) -> tuple[str | None, str | None, bool]:
        """(category, matched words, goal) for the row. Productive and other votes are
        counted per side; the side with more than half of the deciding frames wins and
        its most voted category is the verdict (frames that decided nothing, such as a
        full-screen talking head, don't vote). So one odd screenshot cannot flip a long
        stretch. The goal needs a keyword on at least a third of the frames."""
        category = None
        total = sum(self.votes.values())
        for side in (True, False):
            side_votes = Counter({c: n for c, n in self.votes.items() if (c in PRODUCTIVE) == side})
            if side_votes and sum(side_votes.values()) * 2 > total:
                category = side_votes.most_common(1)[0][0]
        terms = ", ".join(t for t, _ in self.terms[category].most_common(MAX_TERMS)) if category else None
        return category, terms, self.goal_frames * 3 >= self.frames > 0


class OcrWorker:
    """Background reader. submit() is called from the agent thread and never blocks."""

    def __init__(
        self,
        store: Store,
        engine: Engine,
        content_loader: Callable[[], dict[str, list[str]]],
        rules_mtime: Callable[[], float | None] = lambda: None,
        clock: Callable[[], float] | None = None,
    ):
        self.store = store
        self.engine = engine
        self._load_content = content_loader
        self._rules_mtime = rules_mtime
        self._clock = clock or time.monotonic
        self._matcher = ContentMatcher(content_loader())
        self._mtime = rules_mtime()
        self._checked = self._clock()
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._tallies: OrderedDict[int, _Tally] = OrderedDict()
        self._lock = threading.Lock()  # process() vs drop_pending()
        self._generation = 0
        self._failures = 0
        self.enabled = True
        self._thread: threading.Thread | None = None

    # agent side
    def submit(self, shot_id: int, path: Path, day: str) -> None:
        if not self.enabled:
            return
        try:
            self._queue.put_nowait((self._generation, shot_id, path, day))
        except queue.Full:
            log.debug("OCR queue full; skipping a frame")

    def drop_pending(self) -> None:
        """Forget queued frames and tallies (after a delete)."""
        with self._lock:
            self._generation += 1
            self._tallies.clear()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    # worker side
    def start(self, stop: threading.Event) -> None:
        self._thread = threading.Thread(target=self._run, args=(stop,), daemon=True, name="ocr")
        self._thread.start()

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, stop: threading.Event) -> None:
        _lower_thread_priority()
        while not stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.process(item)
            except Exception:
                log.exception("OCR step failed")

    def process(self, item: tuple) -> None:
        generation, shot_id, path, day = item
        if generation != self._generation or not self.enabled:
            return
        self._maybe_reload_rules()
        try:
            text = self.engine.read(path)
        except FileNotFoundError:
            return  # wiped or deleted meanwhile
        except Exception as exc:
            self._failures += 1
            log.warning("OCR could not read a screenshot (%s)", type(exc).__name__)
            if self._failures >= MAX_FAILURES:
                self.enabled = False
                log.error("OCR turned off after %d failures in a row", self._failures)
            return
        self._failures = 0
        category, terms = self._matcher.classify(text)
        goal = self.store.get_goal(day)
        on_goal = goal_on_screen(text, parse_keywords(goal[1] if goal else None))
        del text  # never kept
        with self._lock:
            if generation != self._generation:
                return
            activity_id = self.store.activity_for_screenshot(shot_id)
            if activity_id is None:
                return
            tally = self._tallies.pop(activity_id, None) or _Tally()
            self._tallies[activity_id] = tally
            while len(self._tallies) > TRACKED_ACTIVITIES:
                self._tallies.popitem(last=False)
            tally.add(category, terms, on_goal)
            # Re-checks that the screenshot is still live and still linked, in one statement.
            self.store.set_activity_content(shot_id, *tally.verdict())

    def _maybe_reload_rules(self) -> None:
        now = self._clock()
        if now - self._checked < RULES_RECHECK_S:
            return
        self._checked = now
        mtime = self._rules_mtime()
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._matcher = ContentMatcher(self._load_content())
            except Exception as exc:
                log.warning("Keeping the old screen-text word lists: %s", exc)


class WindowsOcr:
    """Windows.Media.Ocr through the winrt packages (pywinrt 4), using the user's
    display languages. The engine is created on first use, on the OCR thread."""

    def __init__(self) -> None:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine

        if OcrEngine.try_create_from_user_profile_languages() is None:
            raise RuntimeError("no OCR language pack is installed")
        self._ocr_engine, self._bitmap, self._bgra8 = OcrEngine, SoftwareBitmap, BitmapPixelFormat.BGRA8
        self._engine = None

    def read(self, path: Path) -> str:
        if self._engine is None:
            self._engine = self._ocr_engine.try_create_from_user_profile_languages()
        with Image.open(path) as img:
            img = img.convert("RGBA")
        max_dim = int(self._ocr_engine.max_image_dimension)
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim))
        bitmap = self._bitmap.create_copy_from_buffer(
            img.tobytes("raw", "BGRA"), self._bgra8, img.width, img.height
        )
        result = self._engine.recognize_async(bitmap).get()  # blocks this thread only
        return "\n".join(line.text for line in result.lines)


def make_engine() -> Engine | None:
    """The Windows OCR engine, or None (with one log line) where it is unavailable."""
    if sys.platform != "win32":
        log.info("Screen-text reading (OCR) needs Windows; it is off")
        return None
    try:
        return WindowsOcr()
    except Exception as exc:
        log.warning("Screen-text reading (OCR) is off: %s", exc)
        return None


def _lower_thread_priority() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -2)  # THREAD_PRIORITY_LOWEST
    except Exception:
        log.debug("Could not lower the OCR thread priority", exc_info=True)
