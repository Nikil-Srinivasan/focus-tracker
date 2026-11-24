"""Bridge to the Brave extension.

The extension POSTs to http://127.0.0.1:<port>/browser:
  {"focused": {url, title, audible, tabCount} | absent, "windowTitles": [...]}
`focused` is present only while a Brave window has OS focus; `windowTitles` are
the active-tab titles of every normal window. Private windows are never
reported, so a Brave window whose title matches none of them is treated as
private: excluded from the log and from screenshots.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit

from .titles import clean_title

log = logging.getLogger(__name__)

MAX_BODY = 16_384
AUDIBLE_FRESH_S = 60.0


@dataclass(frozen=True)
class TabInfo:
    url: str
    title: str
    audible: bool
    tab_count: int
    received: float


def sanitize_url(url: str, keep_query: bool = False) -> str:
    """Drop credentials, and by default the query string and fragment (tokens live there)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return f"{parts.scheme}://{parts.hostname or ''}" if parts.scheme else ""
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query if keep_query else "", ""))


def domain_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def domain_matches(domain: str, patterns: list[str]) -> bool:
    return any(domain == p or domain.endswith("." + p) for p in patterns)


def titles_match(window_title: str, tab_title: str) -> bool:
    if not tab_title:
        return False
    return window_title.startswith(tab_title) or clean_title(window_title) == clean_title(tab_title)


class BrowserState:
    """Latest tab report from the extension. Written by the HTTP thread, read by the agent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tab: TabInfo | None = None
        self._window_titles: tuple[str, ...] = ()
        self.last_seen = 0.0  # when the extension last reported anything

    def update(self, data: dict, now: float | None = None) -> None:
        now = time.time() if now is None else now
        titles = data.get("windowTitles", [])
        if not isinstance(titles, list):
            raise ValueError("windowTitles must be a list")
        window_titles = tuple(str(t)[:1024] for t in titles[:100])
        focused = data.get("focused")
        tab = None
        if isinstance(focused, dict) and not focused.get("incognito"):
            tab = TabInfo(
                url=str(focused.get("url", ""))[:2048],
                title=str(focused.get("title", ""))[:1024],
                audible=bool(focused.get("audible", False)),
                tab_count=int(focused.get("tabCount", 0) or 0),
                received=now,
            )
        with self._lock:
            if tab is not None:
                self._tab = tab
            self._window_titles = window_titles
            self.last_seen = now

    def tab_for_window(self, window_title: str) -> TabInfo | None:
        with self._lock:
            tab = self._tab
        if tab is not None and titles_match(window_title, tab.title):
            return tab
        return None

    def is_known_window(self, window_title: str) -> bool:
        """True if the title belongs to a normal (non-private) window the extension reported."""
        with self._lock:
            tab, titles = self._tab, self._window_titles
        if tab is not None and titles_match(window_title, tab.title):
            return True
        return any(titles_match(window_title, t) for t in titles)

    def audible(self, now: float) -> bool:
        with self._lock:
            tab = self._tab
        return tab is not None and tab.audible and now - tab.received <= AUDIBLE_FRESH_S


class _Handler(BaseHTTPRequestHandler):
    state: BrowserState  # set on the subclass

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        origin = self.headers.get("Origin")
        # Web pages always send their Origin on cross-site POSTs; only the extension may talk to us.
        if origin is not None and not origin.startswith("chrome-extension://"):
            self.send_error(403)
            return
        if self.path != "/browser" or "application/json" not in self.headers.get("Content-Type", ""):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self.send_error(413)
            return
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            self.state.update(data)
        except (ValueError, TypeError):
            self.send_error(400)
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:  # keep URLs out of the log
        return


def start_server(state: BrowserState, port: int) -> ThreadingHTTPServer | None:
    handler = type("Handler", (_Handler,), {"state": state})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        log.exception("Could not listen on 127.0.0.1:%d; browser tab tracking is off", port)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="browser-bridge").start()
    log.info("Browser bridge listening on 127.0.0.1:%d", port)
    return server
