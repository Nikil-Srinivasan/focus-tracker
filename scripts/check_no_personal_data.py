#!/usr/bin/env python3
"""Refuse to commit screenshots, databases, logs or the data folder.

Used as a git pre-commit hook (checks staged files) and in CI (`--all` checks
every tracked file). This repo is public; personal data must never reach it.
"""

from __future__ import annotations

import subprocess
import sys

BLOCKED_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log",
)
BLOCKED_PREFIXES = ("data/",)
BLOCKED_NAMES = ("config.toml",)


def blocked(paths: list[str]) -> list[str]:
    bad = []
    for p in paths:
        norm = p.replace("\\", "/")
        low = norm.lower()
        if (
            low.endswith(BLOCKED_SUFFIXES)
            or low.startswith(BLOCKED_PREFIXES)
            or low.rsplit("/", 1)[-1] in BLOCKED_NAMES
        ):
            bad.append(norm)
    return bad


def main(argv: list[str]) -> int:
    if "--all" in argv:
        cmd = ["git", "ls-files"]
    else:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    paths = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines()
    bad = blocked(paths)
    if bad:
        print("Blocked: these files may contain personal data and must not be committed:")
        for p in bad:
            print(f"  {p}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
