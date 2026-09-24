#!/usr/bin/env python3
"""
Engine Runtime Helpers
Process-lifetime and capture-loop plumbing shared by main.py's two
processing loops: parent-liveness watch, SIGTERM handling, capture
reconnect with backoff, and deadline-based frame pacing
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import signal
import time


# ============================================================================
# PROCESS LIFETIME
# ============================================================================
def _raise_keyboard_interrupt(signum, frame):
    """SIGTERM handler: unwind exactly like the launcher's SIGINT Stop"""
    raise KeyboardInterrupt


def install_sigterm_handler():
    """
    Treat SIGTERM like SIGINT so run()'s `finally:` cleanup always runs

    Python's default SIGTERM action kills the process on the spot - no
    `finally:`, so the landmarkers, camera and OSC sender are never released
    and receivers never see the clear-on-stop. The launcher escalates to
    SIGTERM when a SIGINT Stop hangs, and `kill` sends it by default.
    Must be called from the main thread (signal.signal's rule).
    """
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)


class ParentWatch:
    """
    Detects the launcher going away underneath a GUI-spawned engine

    Nothing else ties the engine's lifetime to the launcher's: a force-quit
    (or crash) of the launcher left the engine holding the camera and
    streaming OSC with no window to stop it from, and the next Start failed
    with "Video capture is not open" (#34). When the parent dies the kernel
    reparents us (to launchd on macOS), so getppid() changing is the signal.
    """

    CHECK_INTERVAL = 1.0  # Seconds between getppid() checks

    def __init__(self, enabled, clock=time.monotonic):
        """
        Args:
            enabled: Watch only when launched from the GUI - a CLI run's
                parent is a shell, whose exit shouldn't stop tracking
            clock: Monotonic time source (injectable for tests)
        """
        self._clock = clock
        self.parent_pid = os.getppid() if enabled else None
        self._next_check = 0.0

    def parent_gone(self):
        """True once the original parent has exited (checked ~once a second)"""
        if self.parent_pid is None:
            return False
        now = self._clock()
        if now < self._next_check:
            return False
        self._next_check = now + self.CHECK_INTERVAL
        return os.getppid() != self.parent_pid
