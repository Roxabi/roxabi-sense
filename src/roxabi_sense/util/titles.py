"""Window title normalization (strip Grok spinner / Thinking noise + C0)."""

from __future__ import annotations

import re

# Braille / spinner block often used by Grok TUI in Ghostty titles.
_SPINNER_PREFIX = re.compile(
    r"^(?:"
    r"[\u2800-\u28FF]+"  # braille patterns
    r"|[◐◓◑◒⣾⣽⣻⢿⡿⣟⣯⣷⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠁⠂⠄]*"
    r")\s*",
)
_STATUS_WORDS = (
    r"Thinking|Responding|Running|"
    r"Waiting for response…|Waiting for response\.\.\."
)
_STATUS_PREFIX = re.compile(
    rf"^(?:-\s*)?(?:{_STATUS_WORDS})\s*-\s*",
    re.IGNORECASE,
)
_STATUS_INLINE = re.compile(
    rf"^(?:[\u2800-\u28FF]+\s*)?(?:{_STATUS_WORDS})\s*-\s*",
    re.IGNORECASE,
)
# C0 controls + DEL + ESC (CSI/OSC injection into terminal when printing)
_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_display(text: str, *, max_len: int = 500) -> str:
    """Strip control chars that break terminals/logs; cap length."""
    t = _UNSAFE.sub("", text or "")
    t = t.replace("\x1b", "")
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t


def normalize_title(title: str) -> str:
    """
    Stable title for dedup / display.

    '⠋ - Thinking - Silex-Brain… - grok' → 'Silex-Brain… - grok'
    """
    t = sanitize_display((title or "").strip())
    for _ in range(4):
        nxt = _SPINNER_PREFIX.sub("", t)
        nxt = _STATUS_PREFIX.sub("", nxt)
        nxt = _STATUS_INLINE.sub("", nxt)
        nxt = nxt.lstrip(" -")
        if nxt == t:
            break
        t = nxt
    return t.strip()
