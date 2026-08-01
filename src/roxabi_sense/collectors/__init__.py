"""Signal collectors (primary axis)."""

from roxabi_sense.collectors.agent_sessions import AgentSessionsCollector
from roxabi_sense.collectors.cursor_sessions import CursorSessionsCollector
from roxabi_sense.collectors.focus import FocusAtspiCollector, FocusCollector
from roxabi_sense.collectors.idle import IdleCollector
from roxabi_sense.collectors.mpris import MprisCollector
from roxabi_sense.collectors.process_presence import ProcessPresenceCollector
from roxabi_sense.collectors.tmux_sessions import TmuxSessionsCollector

# Long-lived AT-SPI runtime lives in roxabi_sense.atspi (not a fact collector).

__all__ = [
    "AgentSessionsCollector",
    "CursorSessionsCollector",
    "FocusAtspiCollector",
    "FocusCollector",
    "IdleCollector",
    "MprisCollector",
    "ProcessPresenceCollector",
    "TmuxSessionsCollector",
]
