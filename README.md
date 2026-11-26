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
| 2 | Brave extension (tabs, URLs), exact switch times, Watching state, categories | done |
| 3 | Local dashboard (Today, Focus, Distractions, Replay, Settings), break prompt, pause hotkey | done |
| 4 | Weekly plan engine (top 3 actions, ranked by hours recovered), optional AI write-up | done |
| 5 | Screen-text reading (local OCR) to tell a YouTube lecture from entertainment | **this release** |

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

Categories: Learning, Job search, Tech/Work, Study tools (productive),
Communication, Entertainment, and **Other** (not sure). Each stretch is sorted in
this order, first answer wins:

1. **Your rules** in `rules.toml` at the repo root (git-ignored), including what
   you pick on the Settings page.
2. **What was on screen** (see [Screen text](#screen-text-ocr)). The same app or
   site can be work or play, so this beats the built-in rules.
3. **Built-in rules** in `app\focus_tracker\default_rules.toml` (VS Code, LeetCode,
   the LMS, job sites, WhatsApp, Netflix, ...), for stretches the screen text could
   not decide or that are never screenshotted (the LMS).
4. Otherwise **Other**. Settings lists these pages so you can pick a category for
   that page, or for the whole site or app.

You don't have to tag anything up front. Rules look like this:

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

**Study tools** (Anytype/Obsidian/Notion, the Claude and ChatGPT apps and sites,
Google/Bing search results) serve whatever you are learning, so they need no
keyword: inside an on-goal run (lecture, LeetCode) they count as on-goal and keep
a deep block going for up to 15 min in a row. On their own they cannot start a
block and count as off-goal, so random searches or chats don't count.

## Screen text (OCR)

Titles and sites don't say whether a YouTube video is a DSA lecture or a vlog, or
whether a Brave tab, a PDF or a chat was work. So the tracker reads the text on
its **saved screenshot files** with the OCR built into Windows 11 (offline, no
GPU) and sorts every app and site by it. It never captures the screen for this:
it only opens JPEGs that were already saved, so whatever is never screenshotted
(bank and payment pages, lms.vanakkamdsa.com, private windows, idle, locked,
paused) is never read.

- **Which frames**: every saved screenshot of the monitor showing the window in
  front. Unchanged screens are not saved, so they are not read again. It runs on
  a low-priority background thread, not in the capture loop (about 0.03 s per
  1080p frame).
- **How it decides**: each frame is scored against the `[[content]]` word lists
  in `default_rules.toml`: Learning (tutorial, lecture, algorithm, recursion, ...),
  Tech/Work (import, traceback, pull request, ...), Job search (easy apply,
  years of experience, ...), Communication (type a message, last seen, ...) and
  Entertainment (vlog, lyrics, trailer, prank, ...). A frame needs 3 different
  words of one list and 2 more than the best list on the other side (productive
  vs not). A stretch takes what most of its deciding frames say. If nothing is
  clear, the built-in rules apply, and then it is **Other**.
- **Daily goal**: if a goal keyword is on screen in at least a third of a
  stretch's frames, productive time there counts as on-goal even when the title
  is vague.
- **What is stored**: never the text. Only the verdict and the few list words that
  matched (for example `Learning (algorithm, array, recursion)`) on the activity
  row, shown in Replay. Deleting an hour or a day deletes it with the row.
  Nothing from the screen text goes to the log or to Claude.
- Add your own words in `rules.toml`:

  ```toml
  [[content]]
  category = "Learning"
  words = ["takeuforward", "striver"]
  ```

- Turn it off with `ocr = false` in `config.toml`. It is off by itself if the
  Windows OCR language pack is missing (English is installed with Windows).

## Dashboard

Tray icon > **Open dashboard** (or left-click the icon), or browse to
http://127.0.0.1:8765/. It only listens on your PC and loads nothing from the
internet.

| Page | Answers |
| --- | --- |
| Today | Active time, deep work, switches per hour, distraction, goal fit, current run; the day as a colour ribbon; session cards; break answers |
| Focus | Focus-session lengths, context switches per hour, recovery time, a 4-week hour-of-day heatmap of deep work |
| Distractions | Top distractions and off-goal sites, triggers (what you were on right before), communication pull, rabbit holes |
| Replay | Both screens side by side for any moment of the day, with the window that was in front |
| Settings | Edit tracking settings and privacy lists (saved to `config.toml`, applied at once), colour theme, pages the screen text could not sort (Other: pick a category, saved to `rules.toml`), and deleting recorded data |

Definitions: a **deep block** is 25+ min of on-goal work where no distraction
lasts over 60 s and you are not away over 60 s. Switches ignore flickers under
2 s. Excluded windows count as "Private", neither on-goal nor a distraction.

Pages are in the left sidebar. Confirmations use an in-app dialog, and every
save or delete shows a short confirmation in the bottom-right corner.

**Delete recorded data** (bottom of Settings, in two separate boxes): **Delete the
last hour**, or pick any past day (or today) in the box's own date picker and
**Delete** it to remove everything for that tracker day (day-start
hour to the same hour next morning, local time): activity and switches, idle/lock
states, break answers, the goal, and its screenshots from disk. A confirmation
shows what will go first, and the picker previews it. Saved weekly plans that include the day are rebuilt
without it (any AI review for that week is cleared). Deleting today keeps the
tracker running; it records again from that moment and asks for a new goal.

**Back from a break** (5+ min idle or locked) a small window asks once:
Thinking, Break, Call or Distracted. Ignore it and it closes after 2 minutes.

**Pause hotkey**: `Ctrl+Alt+P` toggles a 15 minute pause: press once to pause, again to resume (`pause_hotkey` in
`config.toml`; "" turns it off). Windows tells the tracker only that this exact
combination was pressed; no other keys are seen or recorded.

## Weekly plan

Every Sunday at 23:59 (before the screenshot wipe, or at the next start if the
PC was off) the tracker saves a plan for the week. The **Weekly plan** page
shows it, plus a live preview of the last 7 days that you can read any time.

1. **Checks** run on the week: biggest time sink, worst trigger (what you were
   on right before a distraction), slow starts, chat/mail pull, rabbit holes,
   late-day collapse and off-goal time.
2. **Cost** in hours per week = the logged minutes plus 10 min refocus per
   interruption (`refocus_penalty_min`).
3. **Score** = hours x consistency (share of days showing the pattern) / effort
   (1 = a setting, 2 = a habit, 3 = your schedule). The top 3 become actions,
   each with one measurable target.
4. **Grading**: next week each target is marked hit, partial (at least half way)
   or missed. Missed twice means the action was wrong, so it is swapped for the
   next best.
5. **Deep-work bar**: the first 2 weeks measure your baseline; after that the
   bar is last week's average + 30 min, up to your goal (`deep_work_goal_h`,
   8 h). It tells you when you have plateaued and which 2 hours have been your
   best for deep work.

With fewer than 5 tracked days in a week the plan is marked **low confidence**.

**Optional AI write-up** (off by default): the page has a "Write AI review"
button, or set `ai_review = true` to run it every Sunday. It calls
`claude -p` through your Claude Code login with no console window, and sends
only the weekly numbers, run through a redaction filter (secrets, card, UPI,
Aadhaar, PAN, IFSC, account, OTP, phone, e-mail). No screenshots, URLs or logs
are ever sent. About one call a week.

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

Settings is split into sub-sections (Tracking, Screenshots, Privacy lists, Not
sure, Appearance, Delete data); pick one on the left to see only its options.
Most everyday settings can also be changed there: screenshot
interval, idle time, day start, deep-work goal, pause hotkey, the daily-goal and
break prompts, AI review, weekly wipe day and time, storage cap, and the
never-recorded / no-screenshot lists. Values are validated, written to
`config.toml` (other keys are kept, comments are not) and applied without a
restart. Moving the wipe day starts the new schedule from its next occurrence.
Built-in protections (private windows, bank and payment pages, the course site)
can be added to from the page but not removed; edit `config.toml` for that.

The colour theme (Match Windows, Light, Slate, Indigo, Deep teal, Graphite) is
picked on the Settings page and remembered by the browser.

## Development

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Tests run on Linux too, using a fake system and capturer; the Windows API smoke
test runs only on Windows (and in CI on `windows-latest`).
