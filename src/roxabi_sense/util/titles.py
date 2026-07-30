"""Window title normalization (strip Grok spinner / Thinking noise)."""

from __future__ import annotations

import re

# Braille / spinner block often used by Grok TUI in Ghostty titles.
_SPINNER_PREFIX = re.compile(
    r"^(?:"
    r"[\u2800-\u28FF]+"  # braille patterns
    r"|[◐◓◑◒⣾⣽⣻⢿⡿⣟⣯⣷⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠁⠂⠄]*"
    r")\s*",
)
_STATUS_PREFIX = re.compile(
    r"^(?:-\s*)?(?:Thinking|Responding|Running|Waiting for response…|Waiting for response\.\.\.)\s*-\s*",
    re.IGNORECASE,
)
# Grok sometimes prefixes mid-title status without trailing " - " pattern first
_STATUS_INLINE = re.compile(
    r"^(?:[\u2800-\u28FF]+\s*)?(?:Thinking|Responding|Waiting for response…|Waiting for response\.\.\.)\s*-\s*",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    """
    Stable title for dedup / display.

    '⠋ - Thinking - Silex-Brain… - grok' → 'Silex-Brain… - grok'
    """
    t = (title or "").strip()
    # Loop: spinner then status may alternate order
    for _ in range(4):
        nxt = _SPINNER_PREFIX.sub("", t)
        nxt = _STATUS_PREFIX.sub("", nxt)
        nxt = _STATUS_INLINE.sub("", nxt)
        nxt = nxt.lstrip(" -")
        if nxt == t:
            break
        t = nxt
    return t.strip()
