#!/usr/bin/env python3
"""
Engine Side of the Launcher IPC (#65)
Owns the engine's end of the socket pair: a writer thread that streams the
10 Hz status line (and replies to control commands), a reader thread that
parses control commands, and the shared-memory preview frame writer. The
protocol is documented in src/ipc_protocol.py.

Threading rules:
    - Only the writer thread writes to the socket, so lines never interleave
      and the processing loop never blocks on a slow launcher.
    - The reader thread never touches engine state. `stop` and `visible` are
      flags; `set` is queued and applied by the processing loop between frames
      (drain_sets), which then replies with ack()/error().
    - The processing loop calls tick() once per iteration; that is the only
      place engine counters are read, so the writer thread works from a
      snapshot and needs no locks around the emitter.

The socket closing means the launcher is gone. That replaces the old
getppid() poll: `closed` is set, on_close fires (main.py has it interrupt the
loop and arms a watchdog so the engine exits within about a second).
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import queue
import signal
import socket
import threading
import time

from .ipc_protocol import STATUS_INTERVAL, FrameWriter, LineReader, encode_message
from .live_keys import coerce_live_value, is_live
from .runtime import ALL_PHASES, FAULT_PHASES

#: A snapshot older than this reads as "the loop is not reporting": rates and
#: detections go to 0 instead of repeating the last values
STALE_AFTER = 1.0

#: Fault phases that describe why the engine is exiting - 'stopped' must not
#: overwrite them on the final status
_TERMINAL_FAULTS = ('no_camera', 'permission_denied', 'port_busy')


# ============================================================================
# ENGINE CHANNEL
# ============================================================================
class EngineChannel:
    """Status out, commands in, frames out - one per engine process"""

    def __init__(self, sock, frame_writer=None, on_close=None, clock=time.monotonic, interval=STATUS_INTERVAL):
        """
        Args:
            sock: The engine's end of the launcher's socket pair
            frame_writer: FrameWriter for the preview, or None (no preview)
            on_close: Callable invoked once, from the reader thread, when the
                launcher closes its end
            clock: Monotonic time source (injectable for tests)
            interval: Seconds between status messages
        """
        # One socket, one timeout: a send gives up on a launcher that stopped
        # reading instead of freezing the writer thread, and the reader just
        # polls again on a quiet socket. (A dup() cannot carry a separate
        # timeout - O_NONBLOCK lives on the shared file description.)
        self._sock = sock
        self._sock.settimeout(1.0)
        # A full per-address status line is several KB and the default
        # AF_UNIX buffer is about 8 KB: room for a few seconds of a busy launcher
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        except OSError:
            pass
        self.frames = frame_writer
        self._on_close = on_close
        self._clock = clock
        self._interval = interval
        self._started = clock()

        self.stop_requested = threading.Event()
        self.closed = threading.Event()
        self.visible = True  # False once the launcher says the preview is hidden

        self._phase = 'opening_camera'
        self._reason = ''
        self._outbox = queue.SimpleQueue()
        self._wake = threading.Event()
        self._closing = False
        self._sets = []  # (key, value, ack_count) awaiting the processing loop
        self._sets_lock = threading.Lock()

        self._snapshot = {}
        self._snapshot_at = None
        self._last_sample = None
        self._latency_sum = 0.0
        self._latency_count = 0
        self._latency_ms = 0.0
        self._frame_marks = []  # (time, frames_total) for the fps window

        self._writer = threading.Thread(target=self._write_loop, name='ipc-writer', daemon=True)
        self._reader = threading.Thread(target=self._read_loop, name='ipc-reader', daemon=True)
        self._writer.start()
        self._reader.start()

    # ------------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------------

    @classmethod
    def from_fd(cls, fd, shm_name=None, frame_size=None, **kwargs):
        """
        Adopt the socket the launcher passed down

        Args:
            fd: File descriptor number (--ipc-fd)
            shm_name: Shared-memory segment name (--ipc-shm), or None
            frame_size: (width, height) of preview frames, normally the
                processing resolution; required with shm_name

        Returns:
            EngineChannel. A preview segment that cannot be created leaves
            the channel working without frames (logged, not fatal).
        """
        sock = socket.socket(fileno=fd)
        writer = None
        if shm_name and frame_size:
            try:
                writer = FrameWriter(shm_name, *frame_size)
            except Exception as e:
                print(f"⚠️  Preview frames unavailable: {e}")
        return cls(sock, writer, **kwargs)

    # ------------------------------------------------------------------------
    # Phase and counters (called by the engine)
    # ------------------------------------------------------------------------

    def on_phase(self, phase, reason=""):
        """Phase handler for src.runtime.set_phase_handler; sends a status right away"""
        if phase not in ALL_PHASES:
            return
        self._phase = phase
        self._reason = reason if phase in FAULT_PHASES or phase == 'stopped' else ''
        self._wake.set()

    @property
    def phase(self):
        return self._phase

    def frame_done(self, latency_ms):
        """Record one processed frame's read -> OSC-enqueue time"""
        self._latency_sum += latency_ms
        self._latency_count += 1

    def tick(self, emitter):
        """
        Refresh the snapshot the writer thread reports (call once per loop
        iteration; samples at most once per status interval)

        Args:
            emitter: OscEmitter - the source of fps, queue, dropped, sent,
                per-address counts and detections
        """
        now = self._clock()
        if self._last_sample is not None and now - self._last_sample < self._interval:
            return
        self._last_sample = now
        stats = emitter.stats()
        detected = emitter.take_detections()

        # fps over the last ~1 s of processed frames; the heartbeat's own 1 Hz
        # figure lags too much for a meter
        self._frame_marks.append((now, stats['frames']))
        while len(self._frame_marks) > 2 and now - self._frame_marks[1][0] >= 1.0:
            self._frame_marks.pop(0)
        start_time, start_frames = self._frame_marks[0]
        fps = (stats['frames'] - start_frames) / (now - start_time) if now > start_time else 0.0

        if self._latency_count:
            self._latency_ms = self._latency_sum / self._latency_count
            self._latency_sum, self._latency_count = 0.0, 0
        else:
            self._latency_ms = 0.0

        self._snapshot = {
            'fps': fps,
            'latency_ms': self._latency_ms,
            'detections': {'pose': int(detected.get('pose', 0)), 'hands': int(detected.get('hand', 0))},
            'queue': stats['queue'],
            'dropped': stats['dropped'],
            'sent': stats['sent'],
            'per_address': stats['per_address'],
        }
        self._snapshot_at = now

    # ------------------------------------------------------------------------
    # Frames
    # ------------------------------------------------------------------------

    @property
    def frames_wanted(self):
        """True when a preview segment exists (whether to fill it also depends on `visible`)"""
        return self.frames is not None

    def publish_frame(self, frame, mirror=False):
        """
        Write the drawn display frame to the shared buffer

        Args:
            frame: BGR display frame at processing resolution
            mirror: Flip horizontally first (display only; OSC is unaffected)
        """
        if self.frames is None or frame is None:
            return
        try:
            self.frames.write(frame[:, ::-1] if mirror else frame)
        except Exception as e:
            print(f"⚠️  Preview frame dropped: {e}")

    @property
    def frame_seq(self):
        return self.frames.seq if self.frames is not None else 0

    # ------------------------------------------------------------------------
    # Control commands (applied by the processing loop)
    # ------------------------------------------------------------------------

    @property
    def should_stop(self):
        """True once the launcher asked to stop or went away"""
        return self.stop_requested.is_set() or self.closed.is_set()

    def drain_sets(self):
        """
        Returns:
            List of (key, value, ack_count) for the live `set` commands that
            arrived since the last call. Repeats of one key are coalesced to
            the latest value; ack_count is how many commands it stands for,
            and the caller must ack() or error() that many times.
        """
        with self._sets_lock:
            pending, self._sets = self._sets, []
        return pending

    def ack(self, key, count=1):
        """Confirm an applied command"""
        for _ in range(count):
            self._outbox.put({'ack': key})
        self._wake.set()

    def error(self, key, message, count=1):
        """Reject a command"""
        for _ in range(count):
            self._outbox.put({'error': message, 'key': key})
        self._wake.set()

    def _handle_command(self, message):
        cmd = message.get('cmd')
        if cmd == 'stop':
            self.stop_requested.set()
            self.ack('stop')
        elif cmd == 'visible':
            self.visible = bool(message.get('value'))
            self.ack('visible')
        elif cmd == 'set':
            key = message.get('key')
            if not isinstance(key, str) or not is_live(key):
                self.error(key, f"{key!r} is not a live key - restart the engine to change it")
                return
            try:
                value = coerce_live_value(key, message.get('value'))
            except ValueError as e:
                self.error(key, f"{key}: {e}")
                return
            with self._sets_lock:
                for i, (pending_key, _, count) in enumerate(self._sets):
                    if pending_key == key:
                        self._sets[i] = (key, value, count + 1)
                        break
                else:
                    self._sets.append((key, value, 1))
        else:
            self.error(cmd, f"unknown command {cmd!r}")

    # ------------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------------

    def status_message(self):
        """The status line for right now"""
        now = self._clock()
        snapshot = dict(self._snapshot)
        stale = self._snapshot_at is None or now - self._snapshot_at > STALE_AFTER
        message = {
            'phase': self._phase,
            'fps': 0.0 if stale else round(snapshot.get('fps', 0.0), 1),
            'latency_ms': 0.0 if stale else round(snapshot.get('latency_ms', 0.0), 1),
            'detections': {'pose': 0, 'hands': 0} if stale else snapshot['detections'],
            'queue': snapshot.get('queue', 0),
            'dropped': snapshot.get('dropped', 0),
            'sent': snapshot.get('sent', 0),
            'per_address': snapshot.get('per_address', {}),
            'uptime': round(now - self._started, 1),
            'frame_seq': self.frame_seq,
        }
        if self._reason:
            message['reason'] = self._reason
        return message

    def _send(self, message):
        """
        Writer thread only

        Returns:
            False if the launcher is gone. A send that merely times out (the
            launcher is alive but not reading for a second) drops the line and
            carries on: a slow reader is not a closed socket, and LineReader
            skips the partial line a timed-out send can leave behind.
        """
        try:
            self._sock.sendall(encode_message(message))
            return True
        except socket.timeout:
            return True
        except (OSError, ValueError):
            self._mark_closed()
            return False

    def _write_loop(self):
        next_status = self._clock()
        while True:
            self._wake.wait(max(0.0, next_status - self._clock()))
            woken = self._wake.is_set()
            self._wake.clear()
            # Read before building the status, so a close() that lands during
            # this pass still gets one more pass carrying the final phase
            closing = self._closing
            while True:
                try:
                    reply = self._outbox.get_nowait()
                except queue.Empty:
                    break
                if not self._send(reply):
                    return
            now = self._clock()
            if woken or now >= next_status or closing:
                if not self._send(self.status_message()):
                    return
                next_status = now + self._interval
            if closing:
                return

    def _read_loop(self):
        lines = LineReader()
        while True:
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                data = b''
            if not data:
                self._mark_closed()
                return
            for message in lines.feed(data):
                self._handle_command(message)

    def _mark_closed(self):
        if self.closed.is_set():
            return
        self.closed.set()
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception as e:
                print(f"⚠️  Close handler failed: {e}")

    # ------------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------------

    def close(self, reason=""):
        """
        Send the final status, then release the socket and the preview segment

        The final phase is 'stopped' unless a terminal fault (no_camera,
        permission_denied, port_busy) already explains the exit. Idempotent.

        Args:
            reason: Why the engine stopped: "" (user stop), "launcher_gone",
                "capture_lost", "crash"...
        """
        if self._closing:
            return
        # Our own shutdown closes the socket; that is not the launcher leaving
        self._on_close = None
        if self._phase not in _TERMINAL_FAULTS:
            self._phase, self._reason = 'stopped', reason
        self._closing = True
        self._wake.set()
        self._writer.join(timeout=1.0)
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        if self.frames is not None:
            self.frames.close()


# ============================================================================
# NO-OP CHANNEL
# ============================================================================
class NullChannel:
    """Stand-in when the engine runs standalone (no --ipc-fd): every call is a no-op"""

    frames = None
    visible = True
    frames_wanted = False
    frame_seq = 0
    phase = ''
    should_stop = False

    def __init__(self):
        self.stop_requested = threading.Event()
        self.closed = threading.Event()

    def on_phase(self, phase, reason=""):
        pass

    def frame_done(self, latency_ms):
        pass

    def tick(self, emitter):
        pass

    def publish_frame(self, frame, mirror=False):
        pass

    def drain_sets(self):
        return []

    def ack(self, key, count=1):
        pass

    def error(self, key, message, count=1):
        pass

    def close(self, reason=""):
        pass


# ============================================================================
# EXIT WHEN THE LAUNCHER IS GONE
# ============================================================================
def exit_on_launcher_loss(channel, grace=0.9, kill=os.kill, hard_exit=os._exit, pid=None):
    """
    Build the channel's on_close handler: make the engine leave within about a second

    The handler raises SIGTERM in this process (the handler installed by
    runtime.install_sigterm_handler unwinds run() through its cleanup, so the
    camera is released and the receivers get the clear-on-stop). A KeyboardInterrupt
    is not delivered while the main thread is inside native code (model load,
    a blocked camera read), so a watchdog also force-exits after `grace`
    seconds, releasing the preview segment first.

    Args:
        channel: The EngineChannel (its frames are unlinked on a forced exit)
        grace: Seconds the orderly shutdown gets
        kill, hard_exit, pid: Injectable for tests

    Returns:
        Callable for EngineChannel(on_close=...)
    """
    def handler():
        def watchdog():
            time.sleep(grace)
            if channel.frames is not None:
                channel.frames.close()
            hard_exit(0)

        threading.Thread(target=watchdog, name='ipc-watchdog', daemon=True).start()
        kill(pid or os.getpid(), signal.SIGTERM)

    return handler
