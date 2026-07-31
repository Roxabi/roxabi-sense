"""Long-lived AT-SPI agent (system python + gi) — focus events + in-process probes."""

from roxabi_sense.atspi.agent import FocusAtspiAgent, probe_once

__all__ = ["FocusAtspiAgent", "probe_once"]
