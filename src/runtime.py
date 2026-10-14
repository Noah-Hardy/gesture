#!/usr/bin/env python3
"""
Engine Runtime Helpers
Process-lifetime and capture-loop plumbing shared by main.py's processing
loop: SIGTERM handling, capture reconnect with backoff, deadline-based frame
pacing, and engine phase reporting (launcher liveness is the IPC socket, see
src/engine_channel.py)
"""

# ============================================================================
# IMPORTS
# ============================================================================
import signal
import time


# ============================================================================
# STARTUP PHASES
# ============================================================================
# The engine's progress between Start and the first processed frame, in order.
# These ids are the status-channel values (#65); the text is the log line.
PHASES = ('opening_camera', 'loading_model', 'warming_up', 'ready')

# Phases after startup: capture faults and the final state. The engine never
# emits 'port_busy' - OSC output is an unbound UDP send, which cannot fail on a
# busy port - but the status vocabulary keeps it so the launcher can decode it.
FAULT_PHASES = ('reconnecting', 'no_camera', 'permission_denied', 'port_busy')
ALL_PHASES = PHASES + FAULT_PHASES + ('stopped',)
_PHASE_TEXT = {
    'opening_camera': "Opening camera…",
    'loading_model': "Loading model…",
    'warming_up': "Warming up…",
    'ready': "Ready",
    'reconnecting': "Reconnecting to the camera…",
    'no_camera': "Camera unavailable",
    'permission_denied': "Camera access denied",
    'port_busy': "OSC port busy",
    'stopped': "Stopped",
}

_phase_handler = None


def set_phase_handler(handler):
    """
    Route phase changes somewhere besides the log (the status channel, #65)

    Args:
        handler: callable(phase, reason) invoked from report_phase, or None
            to remove it. Errors it raises are swallowed - reporting must
            never stop the engine from starting.
    """
    global _phase_handler
    _phase_handler = handler


def report_phase(phase, reason="", message=None):
    """
    The single seam for startup progress: log the phase and notify the handler

    Args:
        phase: One of ALL_PHASES
        reason: Detail for the handler (fault and stopped phases; unused by
            the log line - the caller prints its own explanation)
        message: Log line override, e.g. "Connecting to NDI source…" or
            "Loading pose model (lite)…"; defaults to the phase's text

    Raises:
        ValueError: phase is not one of ALL_PHASES
    """
    if phase not in _PHASE_TEXT:
        raise ValueError(f"unknown startup phase {phase!r}")
    print(message or _PHASE_TEXT[phase], flush=True)
    if _phase_handler is not None:
        try:
            _phase_handler(phase, reason)
        except Exception as e:
            print(f"⚠️  Phase handler failed: {e}")


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

    def set_interval(self, interval):
        """Change the cap while running (live performance.target_fps); 0 = uncapped"""
        self.interval = interval
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

    def __init__(self, cap, reopen, timeout, clock=time.monotonic, sleep=time.sleep, on_state=None):
        """
        Args:
            cap: The opened capture
            reopen: Callable(old_cap) -> new capture or None. Responsible
                for releasing old_cap if it replaces it
            timeout: Seconds a failure streak may last before giving up
                (0 or less = retry forever)
            clock, sleep: Injectable for tests
            on_state: Optional callable(state, reason) told when the capture
                changes state: 'reconnecting' (first failed read of a streak),
                'recovered' (frames are back) and 'gave_up' (lost for good).
                Errors it raises are swallowed. The status channel's
                reconnecting / no_camera phases hang off this (#65).
        """
        self._on_state = on_state
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
                self._notify('recovered', "")
            self.failures = 0
            self._streak_started = None
            self._backoff = self.MIN_BACKOFF
            return ret, frame

        self._on_failure()
        return False, None

    # ------------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------------

    def _notify(self, state, reason):
        if self._on_state is None:
            return
        try:
            self._on_state(state, reason)
        except Exception as e:
            print(f"⚠️  Capture state handler failed: {e}")

    def _on_failure(self):
        now = self._clock()
        self.failures += 1
        if self._streak_started is None:
            self._streak_started = now
            print("⚠️  Capture stopped delivering frames - retrying")
            self._notify('reconnecting', "Capture stopped delivering frames")

        if self.timeout > 0 and now - self._streak_started >= self.timeout:
            print(f"❌ Capture lost for {now - self._streak_started:.0f}s "
                  f"({self.failures} failed reads) - giving up")
            self.gave_up = True
            self._notify('gave_up', f"Capture lost for {now - self._streak_started:.0f}s")
            return

        if self.failures % self.REOPEN_AFTER == 0:
            print(f"🔄 Reopening capture (attempt {self.failures // self.REOPEN_AFTER})")
            try:
                self.cap = self._reopen(self.cap)
            except Exception as e:
                print(f"⚠️  Reopen failed: {e}")

        self._retry_at = self._clock() + self._backoff
        self._backoff = min(self._backoff * 2, self.MAX_BACKOFF)
