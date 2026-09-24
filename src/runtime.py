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


# ============================================================================
# FRAME PACING
# ============================================================================
class FrameClock:
    """
    Deadline-based FPS cap

    The old sleep-then-rebaseline cap measured each interval from *after*
    the previous sleep, so read/inference/waitKey time was added on top of
    every interval and the loop landed below the requested rate. Scheduling
    against `next_deadline += interval` absorbs that overhead instead.
    """

    def __init__(self, interval, clock=time.monotonic, sleep=time.sleep):
        """
        Args:
            interval: Seconds per frame (0 = uncapped, wait() is a no-op)
        """
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._next_deadline = None

    def wait(self):
        """Sleep until this frame's slot, then book the next one"""
        if self.interval <= 0:
            return
        now = self._clock()
        if self._next_deadline is None:
            self._next_deadline = now
        delay = self._next_deadline - now
        if delay > 0:
            self._sleep(delay)
        elif delay < -self.interval:
            # More than a whole frame behind (a stall, a reconnect) - resync
            # rather than bursting through the missed slots back to back
            self._next_deadline = now
        self._next_deadline += self.interval


# ============================================================================
# CAPTURE RECONNECT
# ============================================================================
class ReconnectingCapture:
    """
    Wraps a cv2.VideoCapture / NDICapture with backoff and reopen on loss

    A failed read used to `continue` straight into the next read with no
    sleep - spinning a core at 100% - and then kill the session after a
    fixed failure count (#32). Now consecutive failures back off from
    MIN_BACKOFF to MAX_BACKOFF, every REOPEN_AFTER failures the capture is
    reopened, and only a streak lasting past `timeout` seconds gives up.

    read() never blocks for longer than one IDLE_SLICE while waiting out a
    backoff - it returns (False, None) instead - so the processing loop keeps
    iterating (and anything at its top, like the heartbeat, keeps running)
    for the whole outage. Check `gave_up` after a failed read.
    """

    MIN_BACKOFF = 0.1   # Seconds before the first retry
    MAX_BACKOFF = 2.0   # Backoff ceiling
    IDLE_SLICE = 0.25   # Longest single sleep inside read()
    REOPEN_AFTER = 5    # Consecutive failures between reopen attempts

    def __init__(self, cap, reopen, timeout, clock=time.monotonic, sleep=time.sleep):
        """
        Args:
            cap: The opened capture
            reopen: Callable(old_cap) -> new capture or None. Responsible
                for releasing old_cap if it replaces it
            timeout: Seconds a failure streak may last before giving up
                (0 or less = retry forever)
            clock, sleep: Injectable for tests
        """
        self.cap = cap
        self._reopen = reopen
        self.timeout = timeout
        self._clock = clock
        self._sleep = sleep
        self.failures = 0
        self.gave_up = False
        self._streak_started = None
        self._retry_at = 0.0
        self._backoff = self.MIN_BACKOFF

    # ------------------------------------------------------------------------
    # OpenCV-compatible surface
    # ------------------------------------------------------------------------

    def isOpened(self):
        return self.cap is not None and self.cap.isOpened()

    def getBackendName(self):
        return self.cap.getBackendName()

    def get(self, prop_id):
        return self.cap.get(prop_id) if self.cap is not None else 0.0

    def release(self):
        if self.cap is not None:
            self.cap.release()

    def read(self):
        """
        Read a frame, handling backoff/reopen on failure

        Returns:
            (ret, frame) like cv2.VideoCapture.read(). After a False, check
            `gave_up` to tell "still recovering" from "lost for good".
        """
        now = self._clock()
        if now < self._retry_at:
            self._sleep(min(self._retry_at - now, self.IDLE_SLICE))
            return False, None

        ret, frame = (False, None)
        if self.isOpened():
            ret, frame = self.cap.read()
        if ret:
            if self.failures:
                print(f"✅ Capture recovered after {self.failures} failed read(s)")
            self.failures = 0
            self._streak_started = None
            self._backoff = self.MIN_BACKOFF
            return ret, frame

        self._on_failure()
        return False, None

    # ------------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------------

    def _on_failure(self):
        now = self._clock()
        self.failures += 1
        if self._streak_started is None:
            self._streak_started = now
            print("⚠️  Capture stopped delivering frames - retrying")

        if self.timeout > 0 and now - self._streak_started >= self.timeout:
            print(f"❌ Capture lost for {now - self._streak_started:.0f}s "
                  f"({self.failures} failed reads) - giving up")
            self.gave_up = True
            return

        if self.failures % self.REOPEN_AFTER == 0:
            print(f"🔄 Reopening capture (attempt {self.failures // self.REOPEN_AFTER})")
            try:
                self.cap = self._reopen(self.cap)
            except Exception as e:
                print(f"⚠️  Reopen failed: {e}")

        self._retry_at = self._clock() + self._backoff
        self._backoff = min(self._backoff * 2, self.MAX_BACKOFF)
