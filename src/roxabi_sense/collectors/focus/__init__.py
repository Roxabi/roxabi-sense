"""Focus signal source — probes + collector (primary axis)."""

from roxabi_sense.collectors.focus.collector import FocusCollector, WindowInfo
from roxabi_sense.collectors.focus.protocol import FocusProbe, FocusSource, FocusWindow
from roxabi_sense.collectors.focus.runtime import FocusRuntime
from roxabi_sense.collectors.focus.select import candidate_sources, select_probe
from roxabi_sense.collectors.focus.session import SessionInfo, detect_session

# Historical name
FocusAtspiCollector = FocusCollector

__all__ = [
    "FocusAtspiCollector",
    "FocusCollector",
    "FocusProbe",
    "FocusRuntime",
    "FocusSource",
    "FocusWindow",
    "SessionInfo",
    "WindowInfo",
    "candidate_sources",
    "detect_session",
    "select_probe",
]
