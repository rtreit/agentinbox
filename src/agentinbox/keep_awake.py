"""Prevent the host from sleeping using the Win32 SetThreadExecutionState API.

This works at the user level (no admin rights required) and does not require
control over the host's power plan settings. The daemon calls prevent_sleep()
on startup and allow_sleep() on shutdown.
"""
from __future__ import annotations

import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_active = False


def prevent_sleep() -> bool:
    """Request the OS to stay awake. Returns True on success."""
    global _active
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
        _active = result != 0
        return _active
    except Exception:
        return False


def allow_sleep() -> None:
    """Release the stay-awake request."""
    global _active
    if not _active or sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        _active = False
    except Exception:
        pass
