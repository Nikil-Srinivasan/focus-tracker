"""Ordered regex rules -> category, plus the daily-goal split (On-goal / Off-goal)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .config import ROOT

PRODUCTIVE = {"Tech/Work", "Learning", "Job search"}
UNCATEGORIZED = "Uncategorized"


@dataclass(frozen=True)
class Rule:
    category: str
    app: re.Pattern | None
    title: re.Pattern | None
    url: re.Pattern | None
    match_all: bool = False  # match = "all": every given field must match (default: any)

    def matches(self, process: str, title: str, url: str) -> bool:
        checks = [
            bool(pattern.search(value))
            for pattern, value in ((self.app, process), (self.title, title), (self.url, url))
            if pattern is not None
        ]
        if not checks:
            return False
        return all(checks) if self.match_all else any(checks)


def _compile(raw: dict) -> Rule:
    def rx(key: str) -> re.Pattern | None:
        pattern = raw.get(key) or ""
        return re.compile(pattern, re.IGNORECASE) if pattern else None

    if "category" not in raw:
        raise ValueError(f"rule without a category: {raw}")
    match = str(raw.get("match", "any")).lower()
    if match not in ("any", "all"):
        raise ValueError(f'rule match must be "any" or "all": {raw}')
    return Rule(raw["category"], rx("app"), rx("title"), rx("url"), match == "all")


def parse_rules(text: str) -> list[Rule]:
    return [_compile(r) for r in tomllib.loads(text).get("rule", [])]


def load_rules(user_path: Path | None = None) -> list[Rule]:
    """User rules (rules.toml at the repo root) first, then the defaults."""
    user_path = user_path or ROOT / "rules.toml"
    rules: list[Rule] = []
    if user_path.exists():
        rules += parse_rules(user_path.read_text(encoding="utf-8"))
    rules += parse_rules(resources.files(__package__).joinpath("default_rules.toml").read_text("utf-8"))
    return rules


def categorize(rules: list[Rule], process: str, title: str, url: str = "") -> str:
    for rule in rules:
        if rule.matches(process, title, url):
            return rule.category
    return UNCATEGORIZED


def goal_fit(category: str, process: str, title: str, url: str, keywords: list[str]) -> str:
    """'On-goal', 'Off-goal' or 'Distraction'. Without a goal, productive time is On-goal."""
    if category not in PRODUCTIVE:
        return "Distraction"
    if not keywords:
        return "On-goal"
    haystack = f"{process} {title} {url}".lower()
    return "On-goal" if any(k in haystack for k in keywords) else "Off-goal"


def parse_keywords(raw: str | None) -> list[str]:
    return [k.strip().lower() for k in (raw or "").split(",") if k.strip()]
