"""Window-title clean-up so stats and rules don't split one page into many rows."""

from __future__ import annotations

import re

_COUNTER = re.compile(r"^\(\d+\+?\)\s*")  # "(3) WhatsApp"
_UNSAVED = re.compile(r"^[●•*]\s*")  # "● main.py - repo - Visual Studio Code"
_BROWSER_SUFFIX = re.compile(
    r"\s+[-–—]\s+(Brave|Google Chrome|Microsoft\u200b?\s?Edge|Mozilla Firefox)"
    r"(\s+[-–—].*)?$",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")


def clean_title(title: str) -> str:
    t = title.strip()
    t = _COUNTER.sub("", t)
    t = _UNSAVED.sub("", t)
    t = _BROWSER_SUFFIX.sub("", t)
    return _SPACES.sub(" ", t).strip()
