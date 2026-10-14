#!/usr/bin/env python3
"""
Engine Controller

Owns the tracking-engine subprocess for the Qt interface: builds its argv from
the Config, streams its console output, stops it with SIGINT (escalating to
SIGTERM and SIGKILL if it hangs), and reports lifecycle changes as Qt signals.

The engine is a separate process of the same executable (cv2 and MediaPipe
must not share the GUI's main thread). It is spawned with subprocess so the
IPC socket (src/engine_link.py, #65) can be inherited through pass_fds, which
QProcess cannot do. A reader thread turns its merged stdout/stderr into
`output` lines; the link's reader thread turns status and replies into
`statusReceived`, `phaseChanged` and `liveReply`; a timer polls the shared
preview buffer into `frameReady`. All of it reaches the GUI thread through
queued signals.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtGui import QImage

from src.config import Config, valid_port
from src.engine_link import EngineLink
from src.live_keys import is_live

# ============================================================================
# CONSTANTS
# ============================================================================
TERMINATE_AFTER = 5.0         # Seconds after SIGINT before SIGTERM
KILL_AFTER = 8.0              # Seconds after SIGINT before SIGKILL
WATCHDOG_INTERVAL_MS = 250    # Stop-escalation check interval
FRAME_POLL_MS = 33            # Preview poll interval (~30 Hz); only copies when frame_seq advanced

STATES = ('stopped', 'starting', 'running', 'stopping')

# Engine exit codes (see EXIT_* in main.py) mapped to a console message
ENGINE_EXIT_MESSAGES = {
    2: 'Engine stopped: camera/NDI source was lost',
    3: 'Engine crashed - see the output above for details',
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# COMMAND LINE
# ============================================================================
def engine_program() -> List[str]:
    """
    The executable (and entry script) that runs the engine.

    Frozen builds relaunch themselves; from source the engine is main.py in
    the repo root.

    Returns:
        List of leading argv tokens, before the mode and flags.
    """
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    return [sys.executable, os.path.join(_REPO_ROOT, 'main.py')]


def build_argv(config: Config) -> List[str]:
    """
    Translate the applied configuration into the engine's argv.

    Args:
        config: The Config the interface edits.

    Returns:
        Full argv including the executable.
    """
    cmd = engine_program()
    mode = config.get('ui', 'mode', 'all')
    cmd.append(mode if mode in ('pose', 'hand', 'all') else 'all')

    host = str(config.get('osc', 'host', '') or '').strip()
    if host:
        cmd += ['--host', host]
    port = config.get('osc', 'port')
    if isinstance(port, int) and not isinstance(port, bool):
        cmd += ['--port', str(port)]

    # Camera and NDI are mutually exclusive
    if config.get('camera', 'use_ndi', False):
        cmd.append('--ndi')
        ndi_source = str(config.get('camera', 'ndi_source', '') or '').strip()
        if ndi_source:
            cmd += ['--ndi-source', ndi_source]
    else:
        camera = config.get('camera', 'device_id')
        if isinstance(camera, int) and not isinstance(camera, bool):
            cmd += ['--camera', str(camera)]

    cmd += ['--pose-model', str(config.get('mediapipe', 'pose_model_type', 'lite'))]
    fps_cap = config.get('performance', 'target_fps', 0)
    if isinstance(fps_cap, (int, float)) and fps_cap > 0:
        cmd += ['--fps-cap', str(int(fps_cap))]
    if config.get('performance', 'show_fps', False):
        cmd.append('--fps')

    # Always explicit: the config, not the engine's own default, decides
    cmd.append('--mirror' if config.get('display', 'mirror_preview', False) else '--no-mirror')

    # Backend toggles are mutually exclusive; CPU wins
    if config.get('performance', 'force_cpu', False):
        cmd.append('--force-cpu')
    elif config.get('performance', 'force_gpu', False):
        cmd.append('--force-gpu')
    if config.get('performance', 'no_holistic', False):
        cmd.append('--no-holistic')
    return cmd


def engine_env() -> Dict[str, str]:
    """Environment for the engine child (unbuffered stdout so console lines stream)."""
    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'
    return env


# ============================================================================
# CONTROLLER
# ============================================================================
class EngineController(QObject):
    """
    Supervises the engine subprocess and its IPC link.

    Signals:
        stateChanged(str): 'stopped' | 'starting' | 'running' | 'stopping'.
            'running' begins when the engine reports the 'ready' phase.
        phaseChanged(str, str): engine phase and reason, on change only.
        statusReceived(dict): every status message.
        frameReady(QImage): a preview frame (a new image per frame, owns its pixels).
        liveReply(dict): {"ack": key} / {"error": msg, "key": key} for control commands.
        output(str): console text, one line per emit.
        exited(int): the engine's return code (negative: killed by that signal).
    """

    stateChanged = Signal(str)
    phaseChanged = Signal(str, str)
    statusReceived = Signal(dict)
    frameReady = Signal(QImage)
    liveReply = Signal(dict)
    output = Signal(str)
    exited = Signal(int)

    # Thread -> GUI hops (emitted from reader threads, delivered queued)
    _lineRead = Signal(str)
    _procEnded = Signal(int)
    _statusIn = Signal(object, dict)
    _replyIn = Signal(object, dict)

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self.last_status: Dict[str, Any] = {}
        self._state = 'stopped'
        self._proc: Optional[subprocess.Popen] = None
        self._link: Optional[EngineLink] = None
        self._phase = ('', '')
        self._last_frame_seq = 0
        self._preview_visible = True
        self._stop_requested_at: Optional[float] = None
        self._terminated = False
        self._killed = False
        self._restart_pending = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_INTERVAL_MS)
        self._watchdog.timeout.connect(self._escalate_stop)
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(FRAME_POLL_MS)
        self._frame_timer.timeout.connect(self._poll_frame)
        self._lineRead.connect(self.output)
        self._procEnded.connect(self._on_finished)
        self._statusIn.connect(self._on_status)
        self._replyIn.connect(self._on_reply)

    # ------------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------------
    @property
    def state(self) -> str:
        """Current lifecycle state."""
        return self._state

    def is_running(self) -> bool:
        """True while the engine process is alive (starting, running or stopping)."""
        return self._proc is not None and self._proc.poll() is None

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.stateChanged.emit(state)

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------
    def start(self) -> None:
        """Spawn the engine. No-op while one is already alive."""
        if self._proc is not None:
            if self._proc.poll() is not None:
                self._restart_pending = True  # exited, its exit is still being processed
            return
        host = str(self.config.get('osc', 'host', '') or '').strip()
        if not host:
            self.output.emit('OSC host is required - engine not started')
            return
        if not valid_port(self.config.get('osc', 'port')):
            self.output.emit('Invalid OSC port (must be 0-65535) - engine not started')
            return

        argv = build_argv(self.config)
        # Callbacks carry their link so a late message from a previous run is ignored
        mine = {}
        link = EngineLink(on_status=lambda m: self._statusIn.emit(mine['link'], m),
                          on_reply=lambda m: self._replyIn.emit(mine['link'], m))
        mine['link'] = link
        self.output.emit('Launching: ' + ' '.join(argv))
        try:
            # From source, run in the repo root so the child resolves the same
            # relative config.json. Frozen builds use an absolute config path,
            # and the working directory must not point inside the bundle.
            proc = subprocess.Popen(
                argv + link.argv, pass_fds=link.pass_fds, env=engine_env(),
                cwd=None if getattr(sys, 'frozen', False) else _REPO_ROOT,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            link.close()
            self.output.emit('Failed to start engine: {}'.format(exc))
            self.exited.emit(-1)
            return
        link.spawned()

        self._proc = proc
        self._link = link
        self._phase = ('', '')
        self._last_frame_seq = 0
        self._stop_requested_at = None
        self._terminated = self._killed = False
        self.last_status = {}
        link.send({'cmd': 'visible', 'value': self._preview_visible})
        threading.Thread(target=self._pump, args=(proc,), name='engine-output', daemon=True).start()
        self._set_state('starting')
        self._frame_timer.start()

    def _pump(self, proc: subprocess.Popen) -> None:
        """Reader thread: stream the engine's output line by line, then report its exit."""
        try:
            for raw in iter(proc.stdout.readline, b''):
                self._lineRead.emit(raw.decode('utf-8', 'replace').rstrip('\r\n'))
        except (OSError, ValueError):
            pass
        code = proc.wait()
        self._procEnded.emit(int(code))

    def stop(self) -> None:
        """Ask the engine to stop with SIGINT so it runs its normal cleanup path."""
        if not self.is_running() or self._state == 'stopping':
            return
        self._restart_pending = False
        self._request_stop()

    def restart(self) -> None:
        """Stop the engine (if running) and start it again once it has exited."""
        if not self.is_running():
            self.start()
            return
        self._restart_pending = True
        if self._state != 'stopping':
            self._request_stop()

    def stop_and_wait(self, timeout: float = 12.0) -> bool:
        """
        Stop the engine and block until it exits (used on quit).

        Args:
            timeout: Seconds to wait, escalating SIGINT -> SIGTERM -> SIGKILL.

        Returns:
            True if the engine is no longer running.
        """
        self._restart_pending = False
        if not self.is_running():
            return True
        if self._state != 'stopping':
            self._request_stop()
        deadline = time.monotonic() + timeout
        while self.is_running():
            if time.monotonic() > deadline and not self._killed:
                self._killed = True
                self._proc.kill()
            self._escalate_stop()
            QCoreApplication.processEvents()
            time.sleep(0.02)
        # Let the exit report (queued from the reader thread) land
        settle = time.monotonic() + 2.0
        while self._proc is not None and time.monotonic() < settle:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        return not self.is_running()

    def _request_stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.send_signal(signal.SIGINT)
        except OSError as exc:
            self.output.emit('Failed to signal engine: {}'.format(exc))
        self._stop_requested_at = time.monotonic()
        self._set_state('stopping')
        self._watchdog.start()

    def _escalate_stop(self) -> None:
        """Escalate SIGINT to SIGTERM then SIGKILL if the child hangs."""
        proc = self._proc
        if self._stop_requested_at is None or proc is None or not self.is_running():
            return
        elapsed = time.monotonic() - self._stop_requested_at
        try:
            if elapsed > KILL_AFTER and not self._killed:
                self._killed = True
                self.output.emit('Engine unresponsive - sending SIGKILL')
                proc.kill()
            elif elapsed > TERMINATE_AFTER and not self._terminated:
                self._terminated = True
                self.output.emit('Engine still running - sending SIGTERM')
                proc.terminate()
        except OSError:
            pass

    # ------------------------------------------------------------------------
    # Process exit
    # ------------------------------------------------------------------------
    def _on_finished(self, code: int) -> None:
        link = self._link
        if link is not None:
            # The engine is gone, so only now is it safe to close the link. Its
            # reader has delivered everything it will; take the final status
            # directly (queued copies are dropped once self._link is cleared)
            link.close()
            if link.last_status:
                self._on_status(link, link.last_status)
        self._frame_timer.stop()
        if code < 0:
            if self._terminated or self._killed:
                self.output.emit('Engine had to be force-stopped')
            else:
                self.output.emit('Engine terminated unexpectedly')
        elif code == 0:
            self.output.emit('Engine exited (code 0)')
        else:
            self.output.emit(ENGINE_EXIT_MESSAGES.get(code, 'Engine exited with code {}'.format(code)))
        restart = self._restart_pending
        self._cleanup()
        self._set_state('stopped')
        self.exited.emit(int(code))
        if restart:
            self.start()

    def _cleanup(self) -> None:
        self._watchdog.stop()
        self._restart_pending = False
        self._proc = None
        self._link = None
        self._stop_requested_at = None
        self._terminated = self._killed = False

    # ------------------------------------------------------------------------
    # Status, replies and frames (#65)
    # ------------------------------------------------------------------------
    def _on_status(self, link, msg: dict) -> None:
        if link is not self._link:
            return  # a straggler from a previous run
        self.last_status = msg
        self.statusReceived.emit(msg)
        phase, reason = str(msg.get('phase', '')), str(msg.get('reason', ''))
        if (phase, reason) != self._phase:
            self._phase = (phase, reason)
            if phase == 'ready' and self._state == 'starting':
                self._set_state('running')
            self.phaseChanged.emit(phase, reason)

    def _on_reply(self, link, msg: dict) -> None:
        # Only `set` results are interesting; 'visible' and 'stop' acks are plumbing
        if link is self._link and msg.get('ack') not in ('visible', 'stop'):
            self.liveReply.emit(msg)

    def _poll_frame(self) -> None:
        link = self._link
        if link is None:
            return
        seq = int(link.last_status.get('frame_seq', 0) or 0)
        if seq <= self._last_frame_seq:
            return
        got = link.read_frame()
        if got is None:
            return
        self._last_frame_seq, frame = got
        height, width = frame.shape[:2]
        # QImage over the array's memory, then copied once so the image owns
        # its pixels and outlives the array (consumers may keep it)
        self.frameReady.emit(QImage(frame.data, width, height, frame.strides[0],
                                    QImage.Format.Format_BGR888).copy())

    # ------------------------------------------------------------------------
    # Control channel
    # ------------------------------------------------------------------------
    def send(self, cmd: dict) -> None:
        """Send a control command ({"cmd": "stop" | "set" | "visible", ...}). No-op when not running."""
        if self._link is not None and self.is_running():
            self._link.send(cmd)

    def set_live(self, key: str, value) -> bool:
        """
        Apply a live setting without a restart.

        Args:
            key: Dotted config key.
            value: New value.

        Returns:
            True if it was sent as a live 'set' (the result arrives on
            liveReply); False for restart-class keys or when the engine is
            not running.
        """
        if not self.is_running() or self._link is None or not is_live(key):
            return False
        return self._link.send({'cmd': 'set', 'key': key, 'value': value})

    def set_preview_visible(self, visible: bool) -> None:
        """Tell the engine whether the preview is on screen (it skips drawing and frames when hidden)."""
        visible = bool(visible)
        if visible == self._preview_visible:
            return
        self._preview_visible = visible
        self.send({'cmd': 'visible', 'value': visible})
