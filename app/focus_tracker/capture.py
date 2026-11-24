"""Per-monitor screenshots with near-duplicate skipping."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageChops, ImageStat

from .system import Rect

THUMB_SIZE = (64, 36)


class Capturer(Protocol):
    def grab(self) -> list[tuple[int, Image.Image]]: ...
    def monitor_index_for_rect(self, rect: Rect | None) -> int | None: ...


class MssCapturer:
    """Captures each physical monitor separately. Monitor numbers start at 1.

    mss handles are per thread, so create and use this from the agent thread only.
    """

    def __init__(self) -> None:
        import mss

        self._sct = mss.mss()

    def _monitors(self) -> list[dict]:
        return self._sct.monitors[1:]  # [0] is the union of all monitors

    def grab(self) -> list[tuple[int, Image.Image]]:
        out = []
        for idx, mon in enumerate(self._monitors(), start=1):
            raw = self._sct.grab(mon)
            out.append((idx, Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")))
        return out

    def monitor_index_for_rect(self, rect: Rect | None) -> int | None:
        if rect is None:
            return None
        left, top = rect[0], rect[1]
        for idx, mon in enumerate(self._monitors(), start=1):
            if mon["left"] == left and mon["top"] == top:
                return idx
        return None

    def close(self) -> None:
        self._sct.close()


def thumbnail(img: Image.Image) -> Image.Image:
    return img.convert("L").resize(THUMB_SIZE, Image.Resampling.BILINEAR)


def is_changed(prev_thumb: Image.Image | None, thumb: Image.Image, threshold: float) -> bool:
    """Mean absolute pixel difference (0-255) of small grayscale thumbnails."""
    if prev_thumb is None:
        return True
    diff = ImageChops.difference(prev_thumb, thumb)
    return ImageStat.Stat(diff).mean[0] >= threshold


def shot_relpath(ts: float, monitor: int) -> str:
    """'2026-11-24/10/105634_m1.jpg', always with forward slashes."""
    dt = datetime.fromtimestamp(ts)
    return f"{dt:%Y-%m-%d}/{dt:%H}/{dt:%H%M%S}_m{monitor}.jpg"


def save_jpeg(img: Image.Image, path: Path, quality: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality, optimize=False)
    return path.stat().st_size
