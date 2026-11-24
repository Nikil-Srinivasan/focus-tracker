"""Regex redaction of secrets and personal identifiers in text.

Everything sent to Claude passes through redact_text() first. Only aggregated
weekly numbers and short labels (site/app names, cleaned window titles) are
ever sent; no screenshots, no URLs, no OCR text.

Ported from the old workflow-optimizer masking.py (pattern set), plus Indian
identifiers: card numbers (Luhn-checked), UPI IDs, Aadhaar, PAN, IFSC, bank
account numbers and OTPs. Over-masking is the safe direction.
"""

from __future__ import annotations

import re

MASK = "[MASKED]"

_PATTERN_STRINGS = [
    # key=value / key: value credential assignments
    r"(?i)\b(password|passwd|pwd|secret|token|apikey|api[_-]?key|accountkey"
    r"|sharedaccesskey|sharedaccesssignature|client[_-]?secret|access[_-]?key"
    r"|private[_-]?key|credentials?)\s*[=:]\s*\S{3,}",
    # connection-string fragments
    r"(?i)\b(data\s+source|initial\s+catalog|user\s+id|integrated\s+security)\s*=\s*[^;]{2,}",
    r"(?i)\bjdbc:[a-z0-9]+://\S+",
    # URI with inline credentials: scheme://user:pass@host
    r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]{1,64}:[^/\s@]{1,128}@",
    # well-known token shapes
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
    r"\bAIza[0-9A-Za-z_-]{30,}\b",
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}(?:\.[A-Za-z0-9_-]+)?\b",
    r"(?i)\bbearer\s+[a-z0-9._~+/=-]{16,}",
    r"(?i)\bsig=[a-z0-9%+/=]{16,}",
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----",
    # India
    r"(?i)\b(otp|one[- ]time (?:password|code)|verification code|security code)\D{0,20}\d{4,8}\b",
    r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b",  # Aadhaar (12 digits, first 2-9)
    r"\b[A-Z]{5}\d{4}[A-Z]\b",  # PAN
    r"\b[A-Z]{4}0[A-Z0-9]{6}\b",  # IFSC
    r"(?i)\b(a/c|acct|account)\s*(no\.?|number|#)?\s*[:#-]?\s*\d{9,18}\b",
    r"(?i)\b[a-z0-9._-]{2,64}@(ok(?:axis|hdfcbank|icici|sbi)|ybl|ibl|axl|paytm|upi|apl|yapl|ptsbi|pthdfc|ptaxis|ptyes)\b",
    r"(?<![\d+])(?:\+91[ -]?)?[6-9]\d{9}\b",  # Indian mobile number
    # e-mail addresses
    r"(?i)\b[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+\b",
]
_PATTERNS = [re.compile(p) for p in _PATTERN_STRINGS]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _mask_cards(text: str) -> tuple[str, int]:
    hits = 0

    def repl(m: re.Match) -> str:
        nonlocal hits
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            hits += 1
            return MASK
        return m.group(0)

    return _CARD.sub(repl, text), hits


def redact_text(text: str) -> tuple[str, int]:
    """Replace sensitive spans with [MASKED]. Returns (text, number of hits)."""
    text, hits = _mask_cards(text)
    for p in _PATTERNS:
        text, n = p.subn(MASK, text)
        hits += n
    return text, hits


def is_sensitive(text: str) -> bool:
    """True if the text (as is, or with whitespace removed, as OCR often splits it) matches."""
    condensed = re.sub(r"\s+", "", text)
    return any(redact_text(t)[1] for t in (text, condensed))
