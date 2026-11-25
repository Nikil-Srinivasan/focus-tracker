"""Ordered regex rules -> category, plus the daily-goal split (On-goal / Off-goal)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .config import ROOT

PRODUCTIVE = {"Tech/Work", "Learning", "Job search", "Study tools"}
# Helpers that serve whatever the goal is (notes, AI chat, web search). Without a
# keyword match they are "Support": they keep an on-goal block going but cannot
# start one (metrics.deep_blocks decides, then relabels them On- or Off-goal).
SUPPORT = {"Study tools"}
UNCATEGORIZED = "Other"  # nothing decided: no rule and no confident screen text


@dataclass(frozen=True)
class Rule:
    category: str
    app: re.Pattern | None
    title: re.Pattern | None
    url: re.Pattern | None
    match_all: bool = False  # match = "all": every given field must match (default: any)
    user: bool = False  # from rules.toml (your corrections): beats the screen text

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


def parse_rules(text: str, user: bool = False) -> list[Rule]:
    rules = [_compile(r) for r in tomllib.loads(text).get("rule", [])]
    return [Rule(r.category, r.app, r.title, r.url, r.match_all, True) for r in rules] if user else rules


def _rule_texts(user_path: Path | None) -> list[tuple[str, bool]]:
    """(text, is yours): rules.toml at the repo root (checked first), then the defaults."""
    user_path = user_path or ROOT / "rules.toml"
    texts = [(user_path.read_text(encoding="utf-8"), True)] if user_path.exists() else []
    texts.append((resources.files(__package__).joinpath("default_rules.toml").read_text("utf-8"), False))
    return texts


def load_rules(user_path: Path | None = None) -> list[Rule]:
    """User rules (rules.toml at the repo root) first, then the defaults."""
    return [rule for text, user in _rule_texts(user_path) for rule in parse_rules(text, user)]


def parse_content(text: str) -> dict[str, list[str]]:
    """[[content]] tables: category -> words or phrases that point to it on screen."""
    out: dict[str, list[str]] = {}
    for raw in tomllib.loads(text).get("content", []):
        category, words = raw.get("category"), raw.get("words")
        if not isinstance(category, str) or not isinstance(words, list) or not all(
            isinstance(w, str) for w in words
        ):
            raise ValueError(f"content needs a category and a list of words: {raw}")
        out.setdefault(category, []).extend(w.strip().lower() for w in words if w.strip())
    return out


def load_content(user_path: Path | None = None) -> dict[str, list[str]]:
    """Screen-text word lists from rules.toml and the defaults, merged per category."""
    merged: dict[str, list[str]] = {}
    for text, _user in _rule_texts(user_path):
        for category, words in parse_content(text).items():
            merged.setdefault(category, []).extend(w for w in words if w not in merged.get(category, []))
    return merged


def match_rule(rules: list[Rule], process: str, title: str, url: str = "") -> Rule | None:
    for rule in rules:
        if rule.matches(process, title, url):
            return rule
    return None


def categorize(rules: list[Rule], process: str, title: str, url: str = "") -> str:
    rule = match_rule(rules, process, title, url)
    return rule.category if rule else UNCATEGORIZED


def categorize_with_content(
    rules: list[Rule], process: str, title: str, url: str, content_cat: str | None
) -> str:
    """Your rules (rules.toml) first, then what the screen text decided for this
    stretch, then the built-in rules, then Other."""
    rule = match_rule(rules, process, title, url)
    if rule is not None and rule.user:
        return rule.category
    if content_cat:
        return content_cat
    return rule.category if rule else UNCATEGORIZED


def goal_fit(
    category: str, process: str, title: str, url: str, keywords: list[str], on_screen: bool = False
) -> str:
    """'On-goal', 'Off-goal', 'Support' or 'Distraction'. Without a goal, productive
    time is On-goal. on_screen: a goal keyword was read on the screen most of the time."""
    if category not in PRODUCTIVE:
        return "Distraction"
    if not keywords or on_screen:
        return "On-goal"
    haystack = f"{process} {title} {url}".lower()
    if any(k in haystack for k in keywords):
        return "On-goal"
    return "Support" if category in SUPPORT else "Off-goal"


def parse_keywords(raw: str | None) -> list[str]:
    return [k.strip().lower() for k in (raw or "").split(",") if k.strip()]
