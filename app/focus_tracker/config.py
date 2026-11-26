"""Configuration: defaults, overridden by an optional config.toml at the repo root."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_EXCLUDE_PROCESSES = [
    "1password.exe",
    "bitwarden.exe",
    "keepass.exe",
    "keepassxc.exe",
    "consent.exe",
    "credentialuibroker.exe",
    "logonui.exe",
]
DEFAULT_EXCLUDE_TITLE_KEYWORDS = [
    "inprivate",
    "incognito",
    "private browsing",
    "netbanking",
    "net banking",
    # Indian banks and payment screens
    "onlinesbi",
    "state bank of india",
    "yono",
    "icici",
    "razorpay",
    "paytm",
    "phonepe",
    "google pay",
    "payment",
    "checkout",
]
# Screenshots skipped, but the window title is still logged (time still counts).
DEFAULT_NO_SCREENSHOT_TITLE_KEYWORDS = [
    "vanakkamdsa",
    "vanakkam dsa",
]


DEFAULT_EXCLUDE_DOMAINS = [
    "onlinesbi.sbi",
    "sbi.co.in",
    "icicibank.com",
    "razorpay.com",
    "paytm.com",
    "phonepe.com",
    "pay.google.com",
]
DEFAULT_NO_SCREENSHOT_DOMAINS = ["lms.vanakkamdsa.com"]


@dataclass
class Config:
    data_dir: Path = ROOT / "data"
    screenshot_interval_s: float = 5.0
    window_poll_s: float = 1.0
    idle_threshold_s: float = 300.0
    jpeg_quality: int = 60
    dedupe_threshold: float = 2.0
    wipe_weekday: int = 6  # Monday = 0 ... Sunday = 6
    wipe_time: str = "23:59"
    max_storage_gb: float = 15.0
    day_rollover_hour: int = 4
    ask_daily_goal: bool = True
    exclude_processes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PROCESSES))
    exclude_title_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXCLUDE_TITLE_KEYWORDS)
    )
    no_screenshot_title_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_NO_SCREENSHOT_TITLE_KEYWORDS)
    )
    # Browser extension bridge (phase 2)
    extension_port: int = 5610
    extension_browsers: list[str] = field(default_factory=lambda: ["brave.exe"])
    store_full_url: bool = False  # False keeps scheme://host/path only
    exclude_domains: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_DOMAINS))
    no_screenshot_domains: list[str] = field(
        default_factory=lambda: list(DEFAULT_NO_SCREENSHOT_DOMAINS)
    )
    # No input but audio playing = "watching" (counts as active), not idle.
    detect_media: bool = True
    # Dashboard and prompts (phase 3)
    dashboard_port: int = 8765
    ask_after_break: bool = True  # one-click "what was that break?" after idle/lock
    pause_hotkey: str = "ctrl+alt+p"  # toggles a 15 min pause; "" turns it off
    # Weekly plan (phase 4)
    deep_work_goal_h: float = 8.0  # destination; weekly bars ramp +30 min from your baseline
    refocus_penalty_min: float = 10.0  # cost charged per interruption when ranking findings
    ai_review: bool = False  # also ask Claude (claude -p) to narrate the weekly numbers
    claude_path: str = ""  # optional full path to claude.exe; found automatically when empty
    claude_timeout_s: int = 180
    # Read text on saved screenshots (Windows OCR, local) to sort every app and site
    # by what was on screen and to spot goal keywords. The text is never stored.
    ocr: bool = True

    @property
    def shots_dir(self) -> Path:
        return self.data_dir / "shots"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "focus.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def wipe_hour_minute(self) -> tuple[int, int]:
        hh, mm = self.wipe_time.split(":")
        return int(hh), int(mm)

    def validate(self) -> None:
        if not 0 <= self.wipe_weekday <= 6:
            raise ValueError("wipe_weekday must be 0 (Monday) to 6 (Sunday)")
        hh, mm = self.wipe_hour_minute
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("wipe_time must be HH:MM")
        if self.screenshot_interval_s < 1 or self.window_poll_s <= 0:
            raise ValueError("intervals must be positive (screenshots at least 1 s)")
        if not 1 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be 1 to 95")
        if self.max_storage_gb <= 0:
            raise ValueError("max_storage_gb must be greater than 0")
        if not 0 <= self.day_rollover_hour <= 23:
            raise ValueError("day_rollover_hour must be 0 to 23")
        for name in ("extension_port", "dashboard_port"):
            if not 1024 <= getattr(self, name) <= 65535:
                raise ValueError(f"{name} must be 1024 to 65535")
        if self.extension_port == self.dashboard_port:
            raise ValueError("extension_port and dashboard_port must differ")
        parse_hotkey(self.pause_hotkey)
        if not 0.5 <= self.deep_work_goal_h <= 16:
            raise ValueError("deep_work_goal_h must be 0.5 to 16")
        if not 0 <= self.refocus_penalty_min <= 60:
            raise ValueError("refocus_penalty_min must be 0 to 60")
        if not 30 <= self.claude_timeout_s <= 1800:
            raise ValueError("claude_timeout_s must be 30 to 1800")
        if not isinstance(self.ocr, bool):
            raise ValueError("ocr must be true or false")


HOTKEY_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """"ctrl+alt+p" -> (modifier flags, virtual-key code); "" -> None (off)."""
    if not text.strip():
        return None
    parts = [p.strip().lower() for p in text.split("+")]
    *mods, key = parts
    if not mods or any(m not in HOTKEY_MODIFIERS for m in mods):
        raise ValueError(f"pause_hotkey needs modifiers from {sorted(HOTKEY_MODIFIERS)}: {text!r}")
    if len(key) != 1 or not key.isalnum() or not key.isascii():
        raise ValueError(f"pause_hotkey must end with one letter or digit: {text!r}")
    flags = 0
    for m in mods:
        flags |= HOTKEY_MODIFIERS[m]
    return flags, ord(key.upper())


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.toml"
    cfg = Config()
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ValueError(f"Unknown keys in {path}: {unknown}")
        for key, value in raw.items():
            if key == "data_dir":
                value = Path(value)
                if not value.is_absolute():
                    value = ROOT / value
            setattr(cfg, key, value)
    cfg.exclude_processes = [p.lower() for p in cfg.exclude_processes]
    cfg.exclude_title_keywords = [k.lower() for k in cfg.exclude_title_keywords]
    cfg.no_screenshot_title_keywords = [k.lower() for k in cfg.no_screenshot_title_keywords]
    cfg.extension_browsers = [p.lower() for p in cfg.extension_browsers]
    cfg.exclude_domains = [d.lower() for d in cfg.exclude_domains]
    cfg.no_screenshot_domains = [d.lower() for d in cfg.no_screenshot_domains]
    cfg.validate()
    return cfg


# ---- Editing from the dashboard ------------------------------------------------
# Settings the dashboard may change. Every one is read live by the agent, so a
# saved change applies on the next tick (the pause hotkey is re-registered).
EDITABLE_NUMBERS = {  # key: (min, max)
    "screenshot_interval_s": (1, 600),
    "idle_threshold_s": (60, 7200),
    "max_storage_gb": (1, 2000),
    "deep_work_goal_h": (0.5, 16),
}
EDITABLE_INTS = {"wipe_weekday": (0, 6), "day_rollover_hour": (0, 23)}
EDITABLE_BOOLS = ("ask_daily_goal", "ask_after_break", "ai_review")
EDITABLE_LISTS = (
    "exclude_processes",
    "exclude_title_keywords",
    "exclude_domains",
    "no_screenshot_title_keywords",
    "no_screenshot_domains",
)
EDITABLE = (*EDITABLE_NUMBERS, *EDITABLE_INTS, *EDITABLE_BOOLS, *EDITABLE_LISTS, "wipe_time", "pause_hotkey")
# Built-in protections: the dashboard can add to these lists but never remove a
# built-in entry (fail closed; config.toml stays the expert escape hatch).
BUILT_IN = {
    "exclude_processes": DEFAULT_EXCLUDE_PROCESSES,
    "exclude_title_keywords": DEFAULT_EXCLUDE_TITLE_KEYWORDS,
    "exclude_domains": DEFAULT_EXCLUDE_DOMAINS,
    "no_screenshot_title_keywords": DEFAULT_NO_SCREENSHOT_TITLE_KEYWORDS,
    "no_screenshot_domains": DEFAULT_NO_SCREENSHOT_DOMAINS,
}
MAX_LIST_ITEMS = 200
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_PROCESS_RE = re.compile(r"^[\w][\w .+()-]*\.exe$")
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


def _clean_list(key: str, value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise ValueError(f"{key} must be a list of at most {MAX_LIST_ITEMS} entries")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{key}: every entry must be text")
        item = item.strip().lower()
        if not item:
            continue
        if len(item) > 100 or any(ord(c) < 0x20 or ord(c) == 0x7F for c in item):
            raise ValueError(f"{key}: entries must be 1 to 100 plain characters")
        if key.endswith("_domains"):
            item = item.removeprefix("www.") if item.count(".") > 1 else item
            if not _DOMAIN_RE.match(item):
                raise ValueError(f"{key}: {item!r} is not a site like example.com")
        elif key == "exclude_processes" and not _PROCESS_RE.match(item):
            raise ValueError(f"{key}: {item!r} is not an app like name.exe")
        elif key.endswith("_keywords") and len(item) < 3:
            raise ValueError(f"{key}: keywords need at least 3 characters")
        if item not in out:
            out.append(item)
    return out


def edit_config(cfg: Config, edits: dict) -> dict:
    """Validate dashboard edits against `cfg`. Returns {key: new value} for the keys
    that change; raises ValueError (message safe to show) if anything is invalid.
    `cfg` itself is not touched."""
    unknown = sorted(set(edits) - set(EDITABLE))
    if unknown:
        raise ValueError(f"these settings cannot be changed here: {unknown}")
    changes: dict = {}
    for key, value in edits.items():
        if key in EDITABLE_NUMBERS:
            lo, hi = EDITABLE_NUMBERS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a number from {lo} to {hi}")
            value = int(value) if float(value).is_integer() else round(float(value), 2)
        elif key in EDITABLE_INTS:
            lo, hi = EDITABLE_INTS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a whole number from {lo} to {hi}")
        elif key in EDITABLE_BOOLS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif key in EDITABLE_LISTS:
            value = _clean_list(key, value)
            current = getattr(cfg, key)
            kept = [x for x in BUILT_IN[key] if x in current and x not in value]
            if kept:
                raise ValueError(f"built-in protections cannot be removed here: {', '.join(kept)}")
        elif key == "wipe_time":
            if not isinstance(value, str) or not _HHMM_RE.match(value):
                raise ValueError("wipe_time must be HH:MM")
        elif key == "pause_hotkey":
            if not isinstance(value, str) or len(value) > 40:
                raise ValueError("pause_hotkey must be text like ctrl+alt+p")
            value = value.strip().lower().replace(" ", "")
        if value != getattr(cfg, key):
            changes[key] = value
    replace(cfg, **changes).validate()  # whole-config checks (hotkey, HH:MM range, ...)
    return changes


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # JSON strings are valid TOML basic strings
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def save_config(path: Path, changes: dict) -> None:
    """Merge `changes` into config.toml, keeping every other key it already has.
    Written to a temp file and swapped in, so a crash never leaves half a file."""
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw.update(changes)
    lines = [
        "# Focus tracker settings (git-ignored). Saved from the dashboard's Settings page;",
        "# comments are not kept. Every key is optional: see config.example.toml.",
        "",
    ]
    lines += [f"{key} = {_toml_value(value)}" for key, value in raw.items()]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
