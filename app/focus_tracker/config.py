"""Configuration: defaults, overridden by an optional config.toml at the repo root."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
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
        if not 0 <= self.day_rollover_hour <= 23:
            raise ValueError("day_rollover_hour must be 0 to 23")


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
    cfg.validate()
    return cfg
