# Focus tracker

A local-only focus tracker for Windows. It logs every app and window switch,
takes a screenshot of each monitor every 5 seconds while you are actually at the
keyboard, and (in later phases) turns that into a dashboard and a weekly, ranked
improvement plan.

Nothing leaves your computer. This repository contains code only; your data
lives in a git-ignored `data\` folder.

## Status

| Phase | What | State |
| --- | --- | --- |
| 1 | Collector: windows, idle/lock, screenshots, weekly wipe, tray | done |
| 2 | Brave extension (tabs, URLs), exact switch times, Watching state, categories | **this release** |
| 3 | Local dashboard (Today, Focus, Distractions, Replay) | planned |
| 4 | Weekly plan engine (top 3 actions, ranked by hours recovered) | planned |

## What it records

- **Activity**: one row per continuous stretch in the same app + window title +
  monitor (+ URL for Brave). Switch times are exact (a Windows event hook), with
  a 1 s poll as a safety net. Titles are cleaned: "(3) WhatsApp - Brave" becomes
  "WhatsApp", so counters and unsaved markers do not split the stretch.
- **URL** (Brave, with the extension): scheme, host and path only. Query strings
  and fragments, where tokens live, are dropped (`store_full_url` to keep them).
- **State**: active, watching (no input for 5 min but audio is playing, such as
  a lecture), idle, locked (lock screen, sign-in or UAC prompt), paused.
- **Screenshots**: one JPEG per monitor every 5 s, only while active or
  watching. A monitor that barely changed since its last saved frame is skipped.
- **Daily goal**: the first time you are active each day (a day starts at 4 AM),
  a small window asks for today's goal and on-goal keywords. Used by the
  insights later to tell on-goal work from drift.

Never captured: any keyboard data, the lock screen, password managers, private
windows (including Brave private windows, see below), bank and payment pages
(by title and by domain), or any app/title/domain on the exclude list (see
`config.example.toml`). Excluded windows are logged as `[excluded]`. A separate
no-screenshot list skips screenshots but keeps the title, so the time still counts
(lms.vanakkamdsa.com is on it by default). A monitor showing any excluded,
no-screenshot or private window is skipped even when that window is not in front.

## Brave extension

The extension in `extension\` tells the tracker which tab is in front (URL,
title, audible, tab count) while a Brave window has focus, plus the titles of
the other normal windows. It only talks to `127.0.0.1:5610`, stores nothing, and
is never allowed in private windows. Because of that, a Brave window it does not
know about is treated as private: logged as `[excluded]` and never screenshotted.

Install once:

1. Open `brave://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and pick `C:\Nikil\projects\focus-tracker\extension`.
3. Leave "Allow in Private" off (Brave will not offer it anyway).

The tracker must be running for tabs to be recorded; without the extension it
still logs Brave by window title. If you change `extension_port` in
`config.toml`, change it in `extension\background.js` and `manifest.json` too.
Brave dialogs and picture-in-picture windows also count as "unknown", so they
show as `[excluded]` (privacy errs on the safe side).

## Categories

Each stretch gets a category from ordered regex rules in
`app\focus_tracker\default_rules.toml`: Learning, Job search, Tech/Work
(productive), Communication, Entertainment, Uncategorized. Put your own rules in
`rules.toml` at the repo root (git-ignored); they are checked first:

```toml
[[rule]]
category = "Learning"
url = "takeuforward\\.org"

[[rule]]
category = "Learning"
match = "all"            # every field given must match (default: any)
url = "youtube\\.com"
title = "striver|graph"
```

With today's goal keywords, productive time splits into **On-goal** and
**Off-goal**; everything else is a **Distraction**.

## Storage

About 1 GB per 8 active hours on two 1080p screens. **All screenshots are wiped
every Sunday at 23:59** (or at the next sign-in if the PC was off), and a hard
cap (default 15 GB) deletes the oldest days first. The activity database is kept
and stays small (tens of MB per month).

## Setup (Windows 11)

Requires Python 3.11+ (3.12 recommended) and git.

```powershell
cd C:\Nikil\projects
git clone https://github.com/Nikil-Srinivasan/focus-tracker.git
cd focus-tracker
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# Try it in a console first (Ctrl+C to stop):
.venv\Scripts\python.exe -m focus_tracker --verbose

# Then start it automatically at every sign-in (tray icon, no console):
powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1
```

Check what it recorded today (time per app, category, goal fit, top sites):

```powershell
.venv\Scripts\python.exe -m focus_tracker.stats
```

Remove from startup: `scripts\uninstall_startup.ps1` (data is left in place).

## Tray icon

Green = recording, amber = paused, grey = idle or locked. Right-click for
Pause 15 minutes, Pause 1 hour, Pause until tomorrow, Resume, Open data folder
and Quit.

## Data layout

```
data\
  focus.db                       activity, state, screenshot index, goals (SQLite)
  shots\2026-11-24\10\105634_m1.jpg   day \ hour \ time_monitor
  logs\agent.log
```

## Privacy and the public repo

- `data\`, `config.toml`, `rules.toml`, images, databases and logs are git-ignored.
- `scripts\setup.ps1` installs a pre-commit hook that refuses those files, and CI
  runs the same check, so a screenshot cannot be pushed by accident.
- Screenshots can contain anything on your screen. Turning on Windows Device
  encryption (Settings > Privacy & security > Device encryption) is recommended.

## Configuration

Copy `config.example.toml` to `config.toml` (setup does this) and edit. Unknown
keys are rejected so typos do not silently do nothing.

## Development

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Tests run on Linux too, using a fake system and capturer; the Windows API smoke
test runs only on Windows (and in CI on `windows-latest`).
