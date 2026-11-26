"""Local dashboard: JSON API + static page on http://127.0.0.1:<dashboard_port>.

Safety: binds to 127.0.0.1 only; rejects any Host header other than
127.0.0.1/localhost (blocks DNS rebinding); state-changing POSTs need a per-run
token in a custom header (blocks other web pages, which cannot set it without a
CORS preflight we never answer); strict CSP and no framing.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from . import metrics, plan, review
from .categorize import NEUTRAL, PRODUCTIVE, load_rules
from .config import ROOT, Config
from .db import Store

if TYPE_CHECKING:
    from .agent import Agent

log = logging.getLogger(__name__)

MAX_BODY = 4096
CATEGORIES = ["Tech/Work", "Learning", "Job search", "Study tools", "Communication", "Entertainment", "System"]
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}
_SITE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_APP_RE = re.compile(r"^[\w][\w .+()-]*\.exe$", re.IGNORECASE)


class Dashboard:
    """Everything the HTTP handler needs, kept apart from the socket code so tests can call it."""

    def __init__(
        self,
        cfg: Config,
        store: Store,
        agent: Agent | None = None,
        rules_path: Path | None = None,
        clock=time.time,
    ):
        self.cfg, self.store, self.agent = cfg, store, agent
        self.rules_path = rules_path or ROOT / "rules.toml"
        self.clock = clock
        self.token = secrets.token_urlsafe(32)
        self._rules_lock = threading.Lock()
        self._classifier: metrics.Classifier | None = None
        self._rules_mtime: float | None = -1.0
        self._reviews: dict[str, str] = {}  # end_day -> "running" or an error message
        self._review_lock = threading.Lock()
        self.review_fn = review.write_review  # replaced in tests

    # helpers
    def classifier(self) -> metrics.Classifier:
        with self._rules_lock:
            mtime = self.rules_path.stat().st_mtime if self.rules_path.exists() else None
            if self._classifier is None or mtime != self._rules_mtime:
                self._classifier = metrics.Classifier(load_rules(self.rules_path))
                self._rules_mtime = mtime
            return self._classifier

    def today(self) -> date:
        return metrics.day_of(self.clock(), self.cfg.day_rollover_hour)

    def parse_day(self, raw: str | None) -> date:
        if not raw:
            return self.today()
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ApiError(400, "day must be YYYY-MM-DD") from None

    def report(self, day: date) -> metrics.DayReport:
        return metrics.DayReport(self.store, self.classifier(), day, self.cfg.day_rollover_hour, self.clock())

    # GET endpoints
    def get(self, path: str, query: dict[str, str]) -> dict:
        data = self._get(path, query)
        data["today"] = self.today().isoformat()
        return data

    def _get(self, path: str, query: dict[str, str]) -> dict:
        day = self.parse_day(query.get("day"))
        if path == "/api/today":
            data = self.report(day).today()
            data["paused_until"] = self.agent.paused_until if self.agent else 0
            data["edges"] = metrics.day_edges(self.store, day, self.cfg.day_rollover_hour, self.clock())
            return data
        if path == "/api/focus":
            data = self.report(day).focus()
            data["heatmap"] = metrics.heatmap(self.store, self.classifier(), day, self.cfg.day_rollover_hour)
            return data
        if path == "/api/distractions":
            return self.report(day).distractions()
        if path == "/api/replay":
            return self.replay(day)
        if path == "/api/plan":
            return self.plan(query.get("week"))
        if path == "/api/settings":
            return self.settings()
        if path == "/api/day_summary":
            return self.day_summary(day)
        raise ApiError(404, "not found")

    def replay(self, day: date) -> dict:
        lo, hi = metrics.day_bounds(day, self.cfg.day_rollover_hour)
        rows = self.store.query(
            "SELECT s.id, s.ts, s.monitor, a.process, a.title, COALESCE(a.url, ''), a.content_cat, "
            "a.content_terms FROM screenshot s "
            "LEFT JOIN activity a ON a.id = s.activity_id "
            "WHERE s.deleted = 0 AND s.ts >= ? AND s.ts < ? ORDER BY s.ts, s.monitor",
            (lo, hi),
        )
        labels: dict[str, int] = {}
        frames = []
        for shot_id, ts, monitor, process, title, url, content_cat, terms in rows:
            text = f"{title} ({metrics.Seg(0, 0, process, title, url, '', '').label})" if process else ""
            if text and content_cat:  # why the screen text re-sorted it
                text += f" · screen text: {content_cat} ({terms})"
            idx = labels.setdefault(text, len(labels))
            frames.append([round(ts, 1), monitor, shot_id, idx])
        return {"frames": frames, "labels": list(labels), "bounds": [lo, hi]}

    def plan(self, week: str | None) -> dict:
        """The saved plan for `week` (or the latest), plus a live preview of the last 7 days."""
        if week:
            self.parse_day(week)
        saved = self.store.get_plan(week)
        today = self.today()
        preview = plan.build_plan(self.store, self.classifier(), today, self.cfg.day_rollover_hour, self.clock(),
                                  self.cfg.refocus_penalty_min, self.cfg.deep_work_goal_h)
        out: dict = {"weeks": self.store.plan_days(), "preview": preview, "ai_review": self.cfg.ai_review,
                     "saved": None}
        if saved:
            end_day, data, text = saved
            out["saved"] = json.loads(data)
            out["saved"]["review"] = text
            out["saved"]["review_status"] = self._reviews.get(end_day)
        return out

    def start_review(self, body: dict) -> dict:
        end_day = str(body.get("week", ""))
        self.parse_day(end_day)
        saved = self.store.get_plan(end_day)
        if not saved:
            raise ApiError(404, "no saved plan for that week")
        with self._review_lock:
            if self._reviews.get(end_day) == "running":
                return {"ok": True, "status": "running"}
            if saved[2]:
                raise ApiError(409, "this week already has a review")
            self._reviews[end_day] = "running"
        data = json.loads(saved[1])

        def work() -> None:
            try:
                text = self.review_fn(data, self.cfg.claude_path, self.cfg.claude_timeout_s)
                self.store.set_plan_review(end_day, text)
                self._reviews.pop(end_day, None)
            except Exception as exc:
                log.warning("AI review failed: %s", exc)
                self._reviews[end_day] = f"Failed: {exc}"[:300]

        threading.Thread(target=work, daemon=True, name="review").start()
        return {"ok": True, "status": "running"}

    def settings(self) -> dict:
        now = self.clock()
        cfg = self.cfg
        return {
            "screenshot_interval_s": cfg.screenshot_interval_s,
            "idle_threshold_min": round(cfg.idle_threshold_s / 60, 1),
            "wipe": f"{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][cfg.wipe_weekday]} {cfg.wipe_time}",
            "max_storage_gb": cfg.max_storage_gb,
            "storage_gb": round(self.store.live_screenshot_bytes() / 1024**3, 2),
            "day_starts": f"{cfg.day_rollover_hour:02d}:00",
            "exclude_processes": cfg.exclude_processes,
            "exclude_title_keywords": cfg.exclude_title_keywords,
            "exclude_domains": cfg.exclude_domains,
            "no_screenshot_title_keywords": cfg.no_screenshot_title_keywords,
            "no_screenshot_domains": cfg.no_screenshot_domains,
            "pause_hotkey": cfg.pause_hotkey,
            "rules_file": self.rules_path.name,
            "categories": CATEGORIES,
            "productive": sorted(PRODUCTIVE),
            "neutral": sorted(NEUTRAL),
            "queue": metrics.uncategorized(self.store, self.classifier(), now - 7 * 86400, now),
        }

    def shot_path(self, shot_id: int) -> Path:
        rows = self.store.query("SELECT path FROM screenshot WHERE id = ? AND deleted = 0", (shot_id,))
        if not rows:
            raise ApiError(404, "not found")
        root = self.cfg.shots_dir.resolve()
        path = (root / rows[0][0]).resolve()
        if root not in path.parents or not path.is_file():
            raise ApiError(404, "not found")
        return path

    # POST endpoints
    def post(self, path: str, body: dict) -> dict:
        if path == "/api/tag":
            return self.tag(body)
        if path == "/api/delete":
            return self.delete(body)
        if path == "/api/plan/review":
            return self.start_review(body)
        raise ApiError(404, "not found")

    def tag(self, body: dict) -> dict:
        """Save your category for a page or video (scope "page": that site or app with
        that exact title) or for a whole site or app (scope "all"). Your rules win
        over the screen text and the built-in rules."""
        kind, key, category = body.get("kind"), str(body.get("key", "")).strip().lower(), body.get("category")
        scope = body.get("scope", "all")
        if category not in CATEGORIES:
            raise ApiError(400, "unknown category")
        if scope not in ("page", "all"):
            raise ApiError(400, "scope must be page or all")
        if kind == "site" and _SITE_RE.match(key) and len(key) <= 253:
            field = "url"
            pattern = r"^[a-z]+://([^/]*\.)?" + re.escape(key) + r"(:\d+)?(/|$)"
        elif kind == "app" and _APP_RE.match(key) and len(key) <= 100:
            field = "app"
            pattern = "^" + re.escape(key) + "$"
        else:
            raise ApiError(400, "invalid app or site")
        lines = [f"category = {json.dumps(category)}", f"{field} = {json.dumps(pattern)}"]
        if scope == "page":
            title = body.get("title")
            if (
                not isinstance(title, str)
                or not title.strip()
                or len(title) > 300
                or title == metrics.EXCLUDED_TITLE
                or any(ord(c) < 0x20 or ord(c) == 0x7F for c in title)
            ):
                raise ApiError(400, "invalid title")
            # JSON strings are valid TOML basic strings: quotes, backslashes and
            # control characters in the (untrusted) title are escaped.
            lines += ['match = "all"', f"title = {json.dumps('^' + re.escape(title) + '$', ensure_ascii=False)}"]
        entry = "\n[[rule]]  # added from the dashboard\n" + "\n".join(lines) + "\n"
        with self._rules_lock:
            existing = self.rules_path.read_text(encoding="utf-8") if self.rules_path.exists() else (
                "# Your own category rules, checked before the screen text and the defaults (git-ignored).\n"
            )
            self.rules_path.write_text(existing + entry, encoding="utf-8")
            self._classifier = None
        return {"ok": True}

    def day_summary(self, day: date) -> dict:
        """What deleting `day` would remove, for the confirmation prompt."""
        lo, hi = metrics.day_bounds(day, self.cfg.day_rollover_hour)
        minutes, rows = self.store.query(
            "SELECT COALESCE(SUM(MIN(end_ts, ?) - MAX(start_ts, ?)), 0) / 60.0, COUNT(*) "
            "FROM activity WHERE start_ts < ? AND end_ts > ?", (hi, lo, hi, lo))[0]
        shots = self.store.query(
            "SELECT COUNT(*) FROM screenshot WHERE deleted = 0 AND ts >= ? AND ts < ?", (lo, hi))[0][0]
        has_goal = self.store.get_goal(day.isoformat()) is not None
        states = self.store.query("SELECT COUNT(*) FROM state WHERE start_ts < ? AND end_ts > ?", (hi, lo))[0][0]
        return {
            "day": day.isoformat(),
            "bounds": [lo, hi],
            "activity_min": round(minutes, 1),
            "activity_rows": rows,
            "screenshots": shots,
            "goal": has_goal,
            "plans": self.weeks_with(day),
            "empty": not (rows or shots or has_goal or states),
        }

    def weeks_with(self, day: date) -> list[str]:
        """Saved weekly plans whose 7 days include `day`."""
        last = (day + timedelta(days=6)).isoformat()
        return [w for w in self.store.plan_days() if day.isoformat() <= w <= last]

    def delete(self, body: dict) -> dict:
        if self.agent is None:
            raise ApiError(503, "tracker not running")
        now = self.clock()
        span = body.get("range")
        if span == "day":
            return self.delete_day(body.get("day"))
        if span == "hour":
            lo = now - 3600
        elif span == "today":
            lo = metrics.day_bounds(self.today(), self.cfg.day_rollover_hour)[0]
        else:
            raise ApiError(400, "range must be hour, today or day")
        removed = self.agent.forget(lo, now + 2)  # +2 s covers the tick in flight
        return {"ok": True, "screenshots_removed": removed}

    def delete_day(self, raw) -> dict:
        """Forget one whole tracker day (rollover hour to rollover hour, local time):
        activity, states, break answers, screenshots and the goal. Saved weekly plans
        that include the day are rebuilt without it. The day must be named explicitly."""
        if not isinstance(raw, str) or not raw:
            raise ApiError(400, "day must be YYYY-MM-DD")
        day = self.parse_day(raw)
        if day > self.today():
            raise ApiError(400, "that day has not happened yet")
        weeks = self.weeks_with(day)
        with self._review_lock:
            if any(self._reviews.get(w) == "running" for w in weeks):
                raise ApiError(409, "an AI review for that week is running, try again in a minute")
        lo, hi = metrics.day_bounds(day, self.cfg.day_rollover_hour)
        assert self.agent is not None
        removed = self.agent.forget(lo, hi, day.isoformat())
        for week in weeks:
            rebuilt = plan.build_plan(self.store, self.classifier(), date.fromisoformat(week),
                                      self.cfg.day_rollover_hour, self.clock(),
                                      self.cfg.refocus_penalty_min, self.cfg.deep_work_goal_h)
            self.store.save_plan(week, self.clock(), json.dumps(rebuilt))
            self.store.set_plan_review(week, None)  # the old write-up described the deleted day
            with self._review_lock:
                self._reviews.pop(week, None)
        log.info("Deleted a whole day of data; rebuilt %d weekly plan(s)", len(weeks))
        return {"ok": True, "day": day.isoformat(), "screenshots_removed": removed, "plans_rebuilt": weeks}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "focus-tracker"
        sys_version = ""

        def log_message(self, fmt: str, *args) -> None:  # keep paths and queries out of the log
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _allowed_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") not in self._allowed_hosts():
                self._json(403, {"error": "forbidden host"})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            url = urlsplit(self.path)
            try:
                if url.path in STATIC:
                    name, ctype = STATIC[url.path]
                    text = resources.files(__package__).joinpath("web", name).read_text("utf-8")
                    if name == "index.html":
                        text = text.replace("__TOKEN__", dash.token)
                    self._send(200, text.encode(), ctype)
                    return
                m = re.fullmatch(r"/shot/(\d+)\.jpg", url.path)
                if m:
                    self._send(200, dash.shot_path(int(m.group(1))).read_bytes(), "image/jpeg")
                    return
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                self._json(200, dash.get(url.path, query))
            except ApiError as e:
                self._json(e.status, {"error": str(e)})
            except Exception:
                log.exception("Dashboard request failed")
                self._json(500, {"error": "internal error"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            origin = self.headers.get("Origin")
            if (origin is not None and origin not in {f"http://{h}" for h in self._allowed_hosts()}) or not secrets.compare_digest(
                self.headers.get("X-Focus-Token", ""), dash.token
            ):
                self._json(403, {"error": "forbidden"})
                return
            if "application/json" not in self.headers.get("Content-Type", ""):
                self._json(415, {"error": "json only"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            if not 0 < length <= MAX_BODY:
                self._json(413, {"error": "bad length"})
                return
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ApiError(400, "expected an object")
                self._json(200, dash.post(urlsplit(self.path).path, body))
            except json.JSONDecodeError:
                self._json(400, {"error": "bad json"})
            except ApiError as e:
                self._json(e.status, {"error": str(e)})
            except Exception:
                log.exception("Dashboard request failed")
                self._json(500, {"error": "internal error"})

    return Handler


def start_dashboard(dash: Dashboard, port: int) -> ThreadingHTTPServer | None:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _handler(dash))
    except OSError:
        log.exception("Could not listen on 127.0.0.1:%d; dashboard is off", port)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    log.info("Dashboard on http://127.0.0.1:%d", port)
    return server

