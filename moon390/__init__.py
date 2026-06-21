"""moon390 -- async control library for the Simaudio MOON Neo 390.

Pure-Python (no Home Assistant imports). The HA integration depends on this.
"""

from __future__ import annotations

from .client import Moon390
from .exceptions import (
    MoonCommandError,
    MoonConnectionError,
    MoonError,
    MoonProtocolError,
)
from .models import InputSetup, MediaInfo, MoonState
from .protocol import (
    Cmd,
    Frame,
    Resp,
    build_command,
    build_frame,
    iter_frames,
)

__all__ = [
    "Moon390",
    "MoonState",
    "MediaInfo",
    "InputSetup",
    "MoonError",
    "MoonConnectionError",
    "MoonProtocolError",
    "MoonCommandError",
    "Cmd",
    "Resp",
    "Frame",
    "build_frame",
    "build_command",
    "iter_frames",
]
