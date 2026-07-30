"""Signal collectors (primary axis)."""

from roxabi_sense.collectors.agent_sessions import AgentSessionsCollector
from roxabi_sense.collectors.focus_atspi import FocusAtspiCollector
from roxabi_sense.collectors.idle import IdleCollector
from roxabi_sense.collectors.mpris import MprisCollector
from roxabi_sense.collectors.process_presence import ProcessPresenceCollector
from roxabi_sense.collectors.tmux_sessions import TmuxSessionsCollector

__all__ = [
    "AgentSessionsCollector",
    "FocusAtspiCollector",
    "IdleCollector",
    "MprisCollector",
    "ProcessPresenceCollector",
    "TmuxSessionsCollector",
]
