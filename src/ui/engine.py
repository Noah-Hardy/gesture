#!/usr/bin/env python3
"""
Engine Controller

Owns the tracking-engine subprocess for the Qt interface: builds its argv from
the Config, streams its console output, stops it with SIGINT (escalating to
SIGTERM and SIGKILL if it hangs), and reports lifecycle changes as Qt signals.

The engine is a separate process of the same executable (cv2 and MediaPipe
must not share the GUI's main thread), exactly as the Tk launcher did it.
The status/control/frame channel (#65, src/engine_link.py) plugs into the
`_link_*` seams below; until it is wired the signals and methods it will own
(`phaseChanged`, `statusReceived`, `frameReady`, `send`, `set_live`) stay inert.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtGui import QImage

from src.config import Config, valid_port

# ============================================================================
# CONSTANTS
# ============================================================================
TERMINATE_AFTER = 5.0         # Seconds after SIGINT before SIGTERM
KILL_AFTER = 8.0              # Seconds after SIGINT before SIGKILL
WATCHDOG_INTERVAL_MS = 250    # Stop-escalation check interval
READY_MARKERS = ('Engine ready',)   # main.py prints "Ready" (startup phase) then "🟢 Engine ready"

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
    cmd.append('--preview' if config.get('display', 'show_window', True) else '--no-preview')

    # Backend toggles are mutually exclusive; CPU wins
    if config.get('performance', 'force_cpu', False):
        cmd.append('--force-cpu')
    elif config.get('performance', 'force_gpu', False):
        cmd.append('--force-gpu')
    if config.get('performance', 'no_holistic', False):
        cmd.append('--no-holistic')
    return cmd


def engine_env() -> QProcessEnvironment:
    """Environment for the engine child (unbuffered stdout, accessory-app hint)."""
    env = QProcessEnvironment.systemEnvironment()
    env.insert('PYTHONUNBUFFERED', '1')
    # Tells the engine to run as a macOS accessory app so its preview window
    # doesn't add a second Dock icon (see src.macos_app). The pre-rename name
    # is set too, for an engine that predates GESTURE_*.
    env.insert('GESTURE_LAUNCHED_FROM_GUI', '1')
    env.insert('MPOSC_LAUNCHED_FROM_GUI', '1')
    return env


# ============================================================================
# CONTROLLER
# ============================================================================
class EngineController(QObject):
    """
    Supervises the engine subprocess.

    Signals:
        stateChanged(str): 'stopped' | 'starting' | 'running' | 'stopping'.
        phaseChanged(str, str): startup phase and reason (status channel, #65).
        statusReceived(dict): every status message (#65).
        frameReady(QImage): a preview frame (#65).
        output(str): console text, one line per emit.
        exited(int): the engine's return code.
    """

    stateChanged = Signal(str)
    phaseChanged = Signal(str, str)
    statusReceived = Signal(dict)
    frameReady = Signal(QImage)
    output = Signal(str)
    exited = Signal(int)

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self.last_status: Dict[str, Any] = {}
        self._state = 'stopped'
        self._proc: Optional[QProcess] = None
        self._buffer = ''
        self._stop_requested_at: Optional[float] = None
        self._terminated = False
        self._killed = False
        self._restart_pending = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_INTERVAL_MS)
        self._watchdog.timeout.connect(self._escalate_stop)

    # ------------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------------
    @property
    def state(self) -> str:
        """Current lifecycle state."""
        return self._state

    def is_running(self) -> bool:
        """True while the engine process is alive (starting, running or stopping)."""
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.stateChanged.emit(state)

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------
    def start(self) -> None:
        """Spawn the engine. No-op while one is already alive."""
        if self.is_running():
            return
        host = str(self.config.get('osc', 'host', '') or '').strip()
        if not host:
            self.output.emit('OSC host is required - engine not started')
            return
        if not valid_port(self.config.get('osc', 'port')):
            self.output.emit('Invalid OSC port (must be 0-65535) - engine not started')
            return

        argv = build_argv(self.config)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.setProcessEnvironment(engine_env())
        # From source, run in the repo root so the child resolves the same
        # relative config.json. Frozen builds use an absolute config path, and
        # the working directory must not point inside the bundle.
        if not getattr(sys, 'frozen', False):
            proc.setWorkingDirectory(_REPO_ROOT)
        proc.readyReadStandardOutput.connect(self._on_ready_read)
        proc.errorOccurred.connect(self._on_error)
        proc.finished.connect(self._on_finished)

        self._proc = proc
        self._buffer = ''
        self._stop_requested_at = None
        self._terminated = self._killed = False
        self.last_status = {}
        self.output.emit('Launching: ' + ' '.join(argv))
        self._set_state('starting')
        proc.start(argv[0], argv[1:])

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
        proc = self._proc
        while proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            if time.monotonic() > deadline:
                proc.kill()
            proc.waitForFinished(250)
            self._escalate_stop()
        return not self.is_running()

    def _request_stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        pid = int(proc.processId())
        try:
            if pid > 0:
                os.kill(pid, signal.SIGINT)
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
        if elapsed > KILL_AFTER and not self._killed:
            self._killed = True
            self.output.emit('Engine unresponsive - sending SIGKILL')
            proc.kill()
        elif elapsed > TERMINATE_AFTER and not self._terminated:
            self._terminated = True
            self.output.emit('Engine still running - sending SIGTERM')
            proc.terminate()

    # ------------------------------------------------------------------------
    # Process callbacks
    # ------------------------------------------------------------------------
    def _on_ready_read(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._buffer += bytes(proc.readAllStandardOutput()).decode('utf-8', 'replace')
        *lines, self._buffer = self._buffer.split('\n')
        for line in lines:
            self._handle_line(line.rstrip('\r'))

    def _handle_line(self, line: str) -> None:
        # Until #65 reports phases over the status channel, the ready line
        # printed right before the processing loop is the running signal.
        if self._state == 'starting' and (line.strip() == 'Ready' or any(m in line for m in READY_MARKERS)):
            self._set_state('running')
        self.output.emit(line)

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart and self._proc is not None:
            self.output.emit('Failed to start engine: {}'.format(self._proc.errorString()))
            self._cleanup()
            self._set_state('stopped')
            self.exited.emit(-1)

    def _on_finished(self, code: int, status) -> None:
        if self._buffer:  # flush an unterminated tail
            self.output.emit(self._buffer.rstrip('\r'))
            self._buffer = ''
        crashed = status == QProcess.ExitStatus.CrashExit
        if crashed:
            # A signal death (SIGTERM / SIGKILL / crash); report as -signal when known
            code = -15 if self._terminated and not self._killed else (-9 if self._killed else -1)
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
        if self._proc is not None:
            self._proc.deleteLater()
        self._proc = None
        self._stop_requested_at = None
        self._terminated = self._killed = False

    # ------------------------------------------------------------------------
    # Control channel seams (#65)
    # ------------------------------------------------------------------------
    def send(self, cmd: dict) -> None:
        """Send a control message to the engine. No-op until the link (#65) is wired."""
        return None

    def set_live(self, key: str, value) -> bool:
        """
        Apply a live setting without a restart.

        Args:
            key: Dotted config key.
            value: New value.

        Returns:
            True if it was sent as a live 'set'; False for restart-class keys or
            when the engine is not running. Always False until the link (#65) is wired.
        """
        return False
