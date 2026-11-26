"""Optional weekly write-up by Claude through the Claude Code CLI (`claude -p`).

Off by default. Sends only the aggregated weekly plan numbers (redacted by
masking.redact_text) and never screenshots, URLs or raw logs. Runs without ever
showing a console window:

- resolves the real claude.exe (or node + cli.js) instead of the npm claude.cmd
  shim, which would start cmd.exe (a visible window, an 8191-character limit,
  and mangling of & | ^ % in the prompt);
- uses CREATE_NO_WINDOW (not DETACHED_PROCESS, so child tools inherit the
  hidden console) and a hidden STARTUPINFO;
- passes the prompt through stdin and runs in its own working directory.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .masking import redact_text

log = logging.getLogger(__name__)

PROMPT = """You are writing a brutally honest weekly focus review for one person.
Use ONLY the numbers in the JSON below; never invent a number, site or pattern.
Rules: lead each point with the number; name the exact site, app, hour or pattern;
no praise padding; one clear instruction per point; under 220 words; plain text
with short paragraphs or a numbered list; no headings. If confidence is "low",
say so in one sentence first. Write clock times in 12-hour form with AM/PM
(for example 9 AM or 2:30 PM), never 24-hour. Then explain the top actions in order, how last
week's targets went (if graded), and the deep-work bar for next week.

JSON:
"""


def find_claude(configured: str = "") -> list[str] | None:
    """Command prefix that starts Claude Code without a console shim, or None."""
    if configured:
        return [configured] if Path(configured).is_file() else None
    found = shutil.which("claude.exe") if sys.platform == "win32" else shutil.which("claude")
    if found and not found.lower().endswith((".cmd", ".bat", ".ps1")):
        return [found]
    # npm install: claude.cmd sits next to node_modules/@anthropic-ai/claude-code/cli.js
    shim = shutil.which("claude.cmd") or shutil.which("claude")
    node = shutil.which("node.exe") or shutil.which("node")
    if shim and node:
        cli = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        if cli.is_file():
            return [node, str(cli)]
    native = Path.home() / ".local" / "bin" / "claude.exe"
    if native.is_file():
        return [str(native)]
    return None


def _hidden_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def run_claude(prompt: str, configured: str = "", timeout: int = 180) -> str:
    """Run `claude -p` with the prompt on stdin; return the text result or raise RuntimeError."""
    cmd = find_claude(configured)
    if not cmd:
        raise RuntimeError("Claude Code (claude.exe) not found. Install it and sign in, or set claude_path.")
    with tempfile.TemporaryDirectory(prefix="focus-review-") as cwd:
        try:
            proc = subprocess.run(
                [*cmd, "-p", "--output-format", "json"],
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                cwd=cwd,
                env={**os.environ, "NO_COLOR": "1"},
                **_hidden_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude timed out after {timeout} s") from exc
        except OSError as exc:
            raise RuntimeError(f"could not start claude: {exc}") from exc
    out = (proc.stdout or "").strip()
    try:
        envelope = json.loads(out)
    except json.JSONDecodeError:
        envelope = None
    if isinstance(envelope, dict):
        if envelope.get("is_error") or envelope.get("api_error_status"):
            msg = str(envelope.get("result") or "unknown error").strip()
            if "401" in msg or "authenticate" in msg.lower():
                msg += " (sign in again: run `claude`, then /login)"
            raise RuntimeError(f"claude reported an error: {msg[:300]}")
        if "result" in envelope:
            return str(envelope["result"]).strip()
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or out or "(no output)"
        raise RuntimeError(f"claude exited {proc.returncode}: {detail[:300]}")
    return out


def review_input(plan: dict) -> str:
    """The redacted, aggregated JSON that is sent; nothing else leaves the PC."""
    keep = {k: plan.get(k) for k in (
        "start_day", "end_day", "tracked_days", "confidence", "active_h", "deep_h_per_day", "deep",
        "best_hours", "graded")}
    keep["actions"] = [
        {k: a[k] for k in ("finding", "cost_h", "consistency", "effort", "action")} | {"target": a["target"]}
        for a in plan.get("actions", [])
    ]
    text, _hits = redact_text(json.dumps(keep, ensure_ascii=False, indent=1))
    return text


def write_review(plan: dict, configured: str = "", timeout: int = 180) -> str:
    reply = run_claude(PROMPT + review_input(plan), configured, timeout)
    return redact_text(reply)[0]
