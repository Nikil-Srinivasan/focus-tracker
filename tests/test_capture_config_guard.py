from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

from focus_tracker import capture
from focus_tracker.config import load_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_no_personal_data import blocked  # noqa: E402


def test_dedupe_threshold():
    a = capture.thumbnail(Image.new("RGB", (1920, 1080), (100, 100, 100)))
    b = capture.thumbnail(Image.new("RGB", (1920, 1080), (101, 101, 101)))
    c = capture.thumbnail(Image.new("RGB", (1920, 1080), (140, 140, 140)))
    assert capture.is_changed(None, a, 2.0)
    assert not capture.is_changed(a, b, 2.0)
    assert capture.is_changed(a, c, 2.0)


def test_jpeg_size_is_reasonable(tmp_path):
    img = Image.effect_noise((1920, 1080), 20).convert("RGB")
    size = capture.save_jpeg(img, tmp_path / "x.jpg", 60)
    assert 0 < size < 1_500_000


def test_config_overrides_and_rejects_unknown_keys(tmp_path):
    p = tmp_path / "config.toml"
    data = (tmp_path / "ft").as_posix()
    p.write_text(f'data_dir = "{data}"\nidle_threshold_s = 90\nexclude_processes = ["Foo.EXE"]\n')
    cfg = load_config(p)
    assert cfg.idle_threshold_s == 90 and cfg.data_dir == Path(data)
    assert cfg.exclude_processes == ["foo.exe"]
    p.write_text("idle_treshold_s = 90\n")
    with pytest.raises(ValueError, match="Unknown keys"):
        load_config(p)
    p.write_text('wipe_time = "25:00"\n')
    with pytest.raises(ValueError):
        load_config(p)


def test_personal_data_guard():
    assert blocked(["app/x.py", "README.md", "config.example.toml"]) == []
    assert blocked(["data/focus.db", "shot.JPG", "a/config.toml", "x.db-wal"]) == [
        "data/focus.db", "shot.JPG", "a/config.toml", "x.db-wal",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows APIs")
def test_windows_system_smoke():
    from focus_tracker.system import make_system

    s = make_system()
    assert s.idle_seconds() >= 0
    assert isinstance(s.is_locked(), bool)
    s.foreground()  # may be None on a headless runner; must not raise
