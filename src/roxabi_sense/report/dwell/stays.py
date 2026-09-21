"""Terminal-context stay stats — complementary to attention hop counts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from roxabi_sense.report.segments import FocusSegment, is_terminal_app

# Complementary “I stayed a while” thresholds (not used to drop short hops).
LONG_STAY_S = (120.0, 300.0, 600.0)  # ≥2m, ≥5m, ≥10m


@dataclass(frozen=True)
class TerminalStayStats:
    """Complementary: continuous terminal-context visits (attention grain).

    Answers “how often / how long do I stay on a terminal?” — not multitask hop
    count. Short hops still appear as short visits in ``visits`` / median.
    """

    visits: int
    median_s: float
    mean_s: float
    # Counts of visits meeting each threshold (a 12m visit counts in all three).
    ge_2m: int
    ge_5m: int
    ge_10m: int
    # Time spent in visits of at least that length.
    time_ge_2m_s: float
    time_ge_5m_s: float
    time_ge_10m_s: float
    time_total_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def terminal_stay_stats(
    attention: list[FocusSegment],
    *,
    thresholds_s: tuple[float, ...] = LONG_STAY_S,
) -> TerminalStayStats:
    """Stats over terminal attention visits only (ghostty / unnamed)."""
    t2, t5, t10 = (
        thresholds_s[0] if len(thresholds_s) > 0 else 120.0,
        thresholds_s[1] if len(thresholds_s) > 1 else 300.0,
        thresholds_s[2] if len(thresholds_s) > 2 else 600.0,
    )
    terms = [s for s in attention if is_terminal_app(s.app) and s.duration_s > 0]
    if not terms:
        return TerminalStayStats(
            visits=0,
            median_s=0.0,
            mean_s=0.0,
            ge_2m=0,
            ge_5m=0,
            ge_10m=0,
            time_ge_2m_s=0.0,
            time_ge_5m_s=0.0,
            time_ge_10m_s=0.0,
            time_total_s=0.0,
        )
    durs = [s.duration_s for s in terms]
    total = float(sum(durs))
    return TerminalStayStats(
        visits=len(terms),
        median_s=round(float(median(durs)), 1),
        mean_s=round(total / len(durs), 1),
        ge_2m=sum(1 for d in durs if d >= t2),
        ge_5m=sum(1 for d in durs if d >= t5),
        ge_10m=sum(1 for d in durs if d >= t10),
        time_ge_2m_s=round(sum(d for d in durs if d >= t2), 1),
        time_ge_5m_s=round(sum(d for d in durs if d >= t5), 1),
        time_ge_10m_s=round(sum(d for d in durs if d >= t10), 1),
        time_total_s=round(total, 1),
    )
