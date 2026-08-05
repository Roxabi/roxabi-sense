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


# Min core length before prefix/substring pane matches count (agent_link).
_PANE_TITLE_MIN = 12


def title_core(title: str) -> str:
    """Lowercase normalized title without trailing agent suffix (for pane match)."""
    t = normalize_title(title or "").lower().strip()
    for suf in (" - grok", " - claude"):
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
            break
    return t.rstrip(".…").rstrip()


def score_pane_title(focus_title: str, pane_title: str) -> int:
    """
    How well a focus window title matches a tmux pane_title.

    Ghostty/AT-SPI titles usually mirror ``#{pane_title}`` (after spinner strip).
    Returns 0–100; 0 means no usable match.

    Tiers: exact=100, prefix=90, mid-substring=80, tail-equality=70.
    Callers that attach cwd should early-return only on high-confidence tiers
    (exact / unique prefix); weak tiers are for optional structural tie-break.
    """
    a = title_core(focus_title)
    b = title_core(pane_title)
    if not a or not b:
        return 0
    if a == b:
        return 100
    if len(a) >= _PANE_TITLE_MIN and len(b) >= _PANE_TITLE_MIN:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if longer.startswith(shorter):
            return 90
        if shorter in longer:
            return 80
    # Parent-session tail: equality only (no unanchored containment).
    if " - " in a:
        tail = a.rsplit(" - ", 1)[-1].strip()
        if tail and len(tail) >= _PANE_TITLE_MIN and tail == b:
            return 70
    if " - " in b:
        tail = b.rsplit(" - ", 1)[-1].strip()
        if tail and len(tail) >= _PANE_TITLE_MIN and tail == a:
            return 70
    return 0
