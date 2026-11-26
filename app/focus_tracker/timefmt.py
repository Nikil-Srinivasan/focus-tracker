"""12-hour clock text for everything shown to the user (9:05 AM, 12 PM).

Stored data stays in epoch seconds / 24-hour config values; only display text
goes through here. Built by hand because Windows strftime has no %-I."""

from __future__ import annotations

from datetime import datetime


def hour12(hour: int) -> str:
    """0..23 (wraps) -> '12 AM', '9 AM', '12 PM', '11 PM'."""
    hour %= 24
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def clock12(dt: datetime) -> str:
    """datetime -> '9:05 AM'."""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def hhmm12(value: str) -> str:
    """'23:59' (config HH:MM) -> '11:59 PM'."""
    hh, mm = value.split(":")
    return clock12(datetime(2000, 1, 1, int(hh), int(mm)))
