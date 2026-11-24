"""Entry point: `python -m focus_tracker` (or pythonw for no console window)."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import ROOT, load_config
from .db import Store


def _setup_logging(log_dir: Path, verbose: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_dir / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _single_instance(lock_path: Path):
    """Hold an exclusive lock for the process lifetime; exit if already running."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logging.getLogger(__name__).error("Focus tracker is already running.")
        sys.exit(1)
    return fh


def _show_error(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Focus tracker", 0x10)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="focus_tracker", description=__doc__)
    parser.add_argument("--config", type=Path, help="path to config.toml")
    parser.add_argument("--no-tray", action="store_true", help="run without the tray icon")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except Exception as exc:  # pythonw has no console: leave a trace somewhere visible
        _setup_logging(ROOT / "data" / "logs", args.verbose)
        logging.getLogger(__name__).error("Invalid configuration: %s", exc)
        _show_error(f"Focus tracker could not start.\n\nInvalid configuration:\n{exc}")
        sys.exit(2)
    _setup_logging(cfg.log_dir, args.verbose)
    lock = _single_instance(cfg.data_dir / "agent.lock")  # noqa: F841  keep the handle open

    from .browser import BrowserState, start_server
    from .capture import MssCapturer
    from .goal import prompt_goal
    from .system import make_system

    store = Store(cfg.db_path)
    system = make_system()
    system.start_switch_watcher()
    browser = BrowserState()
    server = start_server(browser, cfg.extension_port)
    agent = Agent(cfg, store, system, MssCapturer, prompt_goal, browser)
    stop = threading.Event()
    worker = threading.Thread(target=agent.run, args=(stop,), name="agent")
    worker.start()
    try:
        if args.no_tray:
            while worker.is_alive():
                worker.join(1)
        else:
            from .tray import run_tray

            run_tray(agent, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if server is not None:
            server.shutdown()
        worker.join(10)
        if not worker.is_alive():
            store.close()


if __name__ == "__main__":
    main()
