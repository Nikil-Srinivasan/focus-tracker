# CLAUDE.md

Local-only focus tracker for Windows 11: logs app/window/tab switches, screenshots
each monitor every 5 s while the user is present, and (later phases) shows a local
dashboard and writes a weekly, ranked improvement plan. Nothing leaves the PC.

## Layout

- `app/focus_tracker/`: Python agent. `agent.py` (1 s tick: state, switches,
  screenshots), `system.py` (Win32 via ctypes: foreground, idle, lock, event hook,
  audio), `capture.py` (mss + JPEG + dedupe), `db.py` (SQLite, WAL),
  `retention.py` (weekly wipe + storage cap), `browser.py` (local bridge for the
  extension), `categorize.py` + `default_rules.toml`, `titles.py`, `tray.py`, `stats.py`.
- `extension/`: Brave MV3 extension, talks only to `http://127.0.0.1:5610/browser`.
- `scripts/`: Windows setup/startup scripts and the personal-data guard.
- `tests/`: pytest with fake system/capturer, runs on Linux and Windows.

## Commands

```
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/check_no_personal_data.py --all
```

## Hard rules (review every change against these)

1. **No keyboard data of any kind**: no keystrokes, key counts, typing speed or
   key hooks. Idle comes from `GetLastInputInfo` only.
2. **Public repo**: never commit `data/`, `config.toml`, `rules.toml`, screenshots,
   images, databases, logs, tokens or personal details. The guard script and CI
   enforce this; do not weaken them.
3. **Fail closed on privacy**: when unsure whether a screen may show something
   excluded (unknown window, unknown monitor, private browser window, errors),
   skip the screenshot. A monitor showing any excluded / no-screenshot / private
   window is skipped even if that window is not in front.
4. **Exclusions**: payment and bank pages are excluded (logged as `[excluded]`,
   never screenshotted). The no-screenshot list keeps the title so time still
   counts. Private/incognito windows are never recorded.
5. **URLs**: store scheme://host/path only by default (no query, fragment or
   credentials). Never log URLs or titles to the log file.
6. **Local only**: servers bind to 127.0.0.1. The bridge accepts only
   `chrome-extension://` origins and JSON bodies up to 16 KB.
7. **Screenshots**: only while active or watching (not idle, locked or paused);
   all screenshots wiped weekly (Sunday 23:59 local, or next start if missed);
   hard storage cap. The activity log is kept.
8. **Claude CLI calls** (later phases): must never flash a console window. Call
   `claude.exe` directly (not the npm `.cmd`), use `CREATE_NO_WINDOW`, pass the
   prompt via stdin, and keep it to about 1-2 calls a day. Metrics are computed
   locally and deterministically; Claude only narrates.

## Conventions

- Python 3.11+, standard library first; Windows APIs via ctypes, guarded so
  tests run on Linux.
- Keep it simple: cover real edge cases, avoid speculative abstraction.
- Every behaviour change gets a test with the fakes in `tests/conftest.py`.
- Config keys are validated; unknown keys are rejected.

## Phase workflow

When implementing a phase: read the requirement from the plan, cover likely
edge cases without overcomplicating, implement, then loop review (bugs, secret
values, optimisation) and test until it works, and only then open the PR.
