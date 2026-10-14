"""
EngineController wired to the engine IPC (#65). Offscreen Qt; the engine is a
small fake script that speaks the real protocol (src/engine_channel.py), so
the controller's spawn/pass_fds, console streaming, status, frames, live
replies and stop escalation run for real without a camera.

GESTURE_ENGINE_SMOKE=1 also runs the real engine (needs a camera and models).
"""
import os
import sys
import textwrap

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ipc_protocol import FrameReader  # noqa: E402
from src.ui import engine as engine_mod  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.engine import EngineController  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_ENGINE = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {root!r})
    from src.engine_channel import EngineChannel

    args = sys.argv
    fd = int(args[args.index('--ipc-fd') + 1])
    shm = args[args.index('--ipc-shm') + 1]
    mode = 'ok'
    if 'fault' in args:
        mode = 'fault'
    if 'ignore_sigint' in args:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    ch = EngineChannel.from_fd(fd, shm_name=shm, frame_size=(8, 6), interval=0.05)
    from src.runtime import install_sigterm_handler
    install_sigterm_handler()
    print("hello from engine", flush=True)
    if mode == 'fault':
        ch.on_phase('permission_denied', 'Camera access is off')
        time.sleep(0.2)
        ch.close('startup_failed')
        sys.exit(1)
    ch.on_phase('loading_model')
    time.sleep(0.2)
    ch.on_phase('ready')
    import numpy as np
    n, visible = 0, True
    try:
        while True:
            for key, value, count in ch.drain_sets():
                if value == 0.99:
                    ch.error(key, 'nope', count)
                else:
                    ch.ack(key, count)
            if ch.visible != visible:
                visible = ch.visible
                print("visible=%s" % visible, flush=True)
            if ch.should_stop:
                break
            n += 1
            ch.publish_frame(np.full((6, 8, 3), (n % 250, 20, 200), np.uint8))
            time.sleep(0.03)
    except KeyboardInterrupt:
        print("cleanup", flush=True)
    ch.close()
    sys.exit(0)
''')


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    script = tmp_path / 'fake_engine.py'
    script.write_text(FAKE_ENGINE.format(root=ROOT))

    def use(*extra):
        monkeypatch.setattr(engine_mod, 'engine_program', lambda: [sys.executable, str(script), *extra])
    return use


@pytest.fixture
def controller(app, tmp_path):
    ctl = EngineController(Config(str(tmp_path / 'config.json')))
    yield ctl
    ctl.stop_and_wait(timeout=5)


def wait_until(predicate, timeout=15.0):
    waited = 0
    while waited < timeout * 1000:
        if predicate():
            return True
        QTest.qWait(30)
        waited += 30
    return predicate()


class Recorder:
    def __init__(self, ctl):
        self.states, self.phases, self.lines, self.codes = [], [], [], []
        self.statuses, self.frames, self.replies = [], [], []
        ctl.stateChanged.connect(self.states.append)
        ctl.phaseChanged.connect(lambda p, r: self.phases.append((p, r)))
        ctl.output.connect(self.lines.append)
        ctl.exited.connect(self.codes.append)
        ctl.statusReceived.connect(self.statuses.append)
        ctl.frameReady.connect(self.frames.append)
        ctl.liveReply.connect(self.replies.append)


def test_lifecycle_status_frames_and_live_replies(controller, fake_engine):
    fake_engine()
    rec = Recorder(controller)
    controller.start()
    assert controller.is_running() and rec.states == ['starting']

    # 'running' comes from the ready phase on the status channel, not from stdout
    assert wait_until(lambda: 'running' in rec.states)
    assert [p for p, _ in rec.phases][-2:] == ['loading_model', 'ready']
    assert controller.last_status['phase'] == 'ready'
    assert any('hello from engine' in ln for ln in rec.lines)
    assert rec.statuses and 'per_address' in rec.statuses[0]

    # Preview frames arrive as owned QImages with the segment's size
    assert wait_until(lambda: len(rec.frames) >= 2)
    image = rec.frames[-1]
    assert isinstance(image, QImage) and (image.width(), image.height()) == (8, 6)
    assert image.pixelColor(0, 0).red() == 200  # BGR order survived the trip

    # Live settings: acks and errors come back on liveReply
    assert controller.set_live('osc.tracking_hold', 0.5) is True
    assert controller.set_live('mediapipe.min_detection_confidence', 0.99) is True
    assert wait_until(lambda: len(rec.replies) == 2)
    assert {'ack': 'osc.tracking_hold'} in rec.replies
    assert any(r.get('key') == 'mediapipe.min_detection_confidence' and 'error' in r for r in rec.replies)
    assert controller.set_live('camera.device_id', 1) is False  # restart-class
    assert controller.set_live('osc.port', 'bad') is True       # the engine rejects the value
    assert wait_until(lambda: len(rec.replies) == 3 and 'error' in rec.replies[-1])

    controller.set_preview_visible(False)
    assert wait_until(lambda: any('visible=False' in ln for ln in rec.lines))

    segment = controller._link.shm_name
    assert controller.stop_and_wait(timeout=10)
    assert rec.states[-1] == 'stopped' and rec.codes == [0]
    assert any('cleanup' in ln for ln in rec.lines)
    assert not controller.is_running()
    assert FrameReader(segment).info() is None  # segment cleaned up
    assert controller.set_live('osc.tracking_hold', 1.0) is False
    controller.send({'cmd': 'noop'})  # no-op once stopped


def test_fault_phase_survives_the_exit(controller, fake_engine):
    fake_engine('fault')
    rec = Recorder(controller)
    controller.start()
    assert wait_until(lambda: rec.states[-1:] == ['stopped'])
    assert rec.codes == [1]
    assert ('permission_denied', 'Camera access is off') in rec.phases
    assert 'running' not in rec.states


def test_restart_spawns_a_fresh_engine(controller, fake_engine):
    fake_engine()
    rec = Recorder(controller)
    controller.start()
    assert wait_until(lambda: 'running' in rec.states)
    first_pid = controller._proc.pid
    controller.restart()
    assert wait_until(lambda: rec.states.count('running') == 2, timeout=20)
    assert controller._proc.pid != first_pid and rec.codes == [0]


def test_stop_escalates_when_sigint_is_ignored(controller, fake_engine, monkeypatch):
    monkeypatch.setattr(engine_mod, 'TERMINATE_AFTER', 0.3)
    fake_engine('ignore_sigint')
    rec = Recorder(controller)
    controller.start()
    assert wait_until(lambda: 'running' in rec.states)
    assert controller.stop_and_wait(timeout=10)
    assert any('SIGTERM' in ln for ln in rec.lines)
    assert rec.codes == [0]  # SIGTERM unwinds through the engine cleanup


def test_visibility_is_sent_at_start(controller, fake_engine):
    fake_engine()
    rec = Recorder(controller)
    controller.set_preview_visible(False)  # window minimized before Start
    controller.start()
    assert wait_until(lambda: any('visible=False' in ln for ln in rec.lines))


def test_missing_executable_reports_failure(controller, monkeypatch):
    monkeypatch.setattr(engine_mod, 'engine_program', lambda: ['/nonexistent/engine'])
    rec = Recorder(controller)
    controller.start()
    assert not controller.is_running() and controller.state == 'stopped'
    assert rec.codes == [-1] and any('Failed to start' in ln for ln in rec.lines)


@pytest.mark.skipif(not os.environ.get('GESTURE_ENGINE_SMOKE'), reason='needs a camera and the models')
def test_real_engine_smoke(controller, tmp_path):
    controller.config.set('ui', 'mode', 'pose')
    controller.config.set('osc', 'port', 9555)
    rec = Recorder(controller)
    controller.start()
    assert wait_until(lambda: 'running' in rec.states, timeout=60)
    assert wait_until(lambda: rec.frames, timeout=10)
    assert controller.last_status['phase'] == 'ready'
    assert controller.stop_and_wait(timeout=15) and rec.codes == [0]


def test_window_hide_show_and_minimize_tell_the_engine(app, tmp_path):
    from src.ui.main_window import MainWindow
    window = MainWindow(Config(str(tmp_path / 'config.json')))
    try:
        window.show()
        app.processEvents()
        assert window.engine._preview_visible is True
        window.hide()
        assert window.engine._preview_visible is False
        window.show()
        assert window.engine._preview_visible is True
        window.showMinimized()
        app.processEvents()
        assert window.engine._preview_visible is False
    finally:
        window._save_timer.stop()
        window.engine.stop_and_wait()
        window.hide()
