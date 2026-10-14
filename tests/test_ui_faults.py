"""Fault state card tests (#67). Run offscreen; skipped when PySide6 cannot import."""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.overlays import faults  # noqa: E402
from src.ui.overlays.faults import FaultState, build_card  # noqa: E402


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


@pytest.fixture
def window(app, tmp_path):
    cfg = Config(str(tmp_path / 'config.json'))
    cfg.set('ui', 'first_run_complete', True)
    w = MainWindow(cfg)
    w.show()
    app.processEvents()
    yield w
    w._save_timer.stop()
    w.engine.stop_and_wait()
    w.hide()
    w.deleteLater()


def card(window):
    return window.faults.overlay.card


def drive(window, state=None, phase=None, reason=''):
    if state:
        window.engine.stateChanged.emit(state)
    if phase:
        window.engine.phaseChanged.emit(phase, reason)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def test_initial_state_is_stopped():
    assert FaultState('stopped').key == 'stopped'


def test_start_shows_loading_then_clears_when_ready():
    s = FaultState()
    s.on_state('starting')
    assert s.key == 'loading'
    for phase in ('opening_camera', 'loading_model', 'warming_up'):
        s.on_phase(phase)
        assert s.key == 'loading' and s.phase == phase
    s.on_phase('ready')
    s.on_state('running')
    assert s.key is None


def test_running_without_phases_clears_loading():
    s = FaultState()
    s.on_state('starting')
    s.on_state('running')
    assert s.key is None


def test_reconnecting_and_recovery():
    s = FaultState('running')
    s.on_phase('reconnecting')
    assert s.key == 'reconnecting'
    s.on_phase('ready')
    assert s.key is None


@pytest.mark.parametrize('fault', ['no_camera', 'permission_denied'])
def test_camera_fault_survives_the_engine_exit(fault):
    s = FaultState()
    s.on_state('starting')
    s.on_phase('opening_camera')
    s.on_phase(fault)
    s.on_state('stopped')          # the engine exits right after reporting the fault
    s.on_phase('stopped')
    assert s.key == fault
    s.on_state('starting')         # the next start clears it
    assert s.key == 'loading'


def test_plain_stop_shows_stopped():
    s = FaultState('running')
    s.on_state('stopping')
    s.on_state('stopped')
    assert s.key == 'stopped'
    s2 = FaultState('running')
    s2.on_phase('reconnecting')
    s2.on_phase('stopped')         # gave up waiting for the camera
    assert s2.key == 'stopped'


def test_port_busy_has_no_card():
    s = FaultState('running')
    s.on_phase('port_busy')
    assert s.key is None
    assert 'port_busy' not in faults.HELP_SLUGS


# ---------------------------------------------------------------------------
# Card content
# ---------------------------------------------------------------------------
def test_card_texts_and_actions(window):
    cfg = window.config
    stopped = build_card('stopped', cfg)
    assert (stopped.title, stopped.glyph, stopped.tone) == ('Stopped', '■', 'dim')
    assert stopped.actions == [('Start engine', 'start')]
    nc = build_card('no_camera', cfg)
    assert nc.title == 'Camera not found' and nc.tone == 'warn' and 'Camera 0' in nc.body
    assert [a for _, a in nc.actions] == ['restart', 'use_ndi']
    assert [a for a, _ in nc.actions] == ['Rescan devices', 'Use NDI instead']
    pd = build_card('permission_denied', cfg)
    assert pd.title == 'Camera access needed' and pd.tone == 'error' and pd.glyph == '⦸'
    assert [a for a, _ in pd.actions] == ['Open System Settings', 'Recheck']
    rc = build_card('reconnecting', cfg)
    assert rc.title == 'Reconnecting' and rc.actions == [('Stop', 'stop')]
    ld = build_card('loading', cfg)
    assert ld.title == 'Starting' and not ld.actions


def test_help_slugs():
    assert faults.HELP_SLUGS == {
        'loading': 'starting', 'reconnecting': 'reconnecting', 'no_camera': 'camera-not-found',
        'permission_denied': 'camera-access-needed', 'stopped': 'stopped'}


def test_loading_step_follows_phase_and_config(window):
    cfg = window.config
    assert build_card('loading', cfg, 'opening_camera').step[0] == 'Opening camera…'
    assert build_card('loading', cfg, 'loading_model').step[0] == 'holistic_landmarker.task'
    assert build_card('loading', cfg, 'warming_up').step[0] == 'Warming up…'
    cfg.set('ui', 'mode', 'pose')
    cfg.set('mediapipe', 'pose_model_type', 'heavy')
    assert build_card('loading', cfg, 'loading_model').step[0] == 'pose_landmarker_heavy.task'
    cfg.set('ui', 'mode', 'hand')
    assert build_card('loading', cfg, 'loading_model').step[0] == 'hand_landmarker.task'
    cfg.set('ui', 'mode', 'all')
    cfg.set('performance', 'no_holistic', True)
    assert build_card('loading', cfg, 'loading_model').step[0] == 'pose_landmarker_heavy.task + hand_landmarker.task'
    cfg.set('camera', 'use_ndi', True)
    cfg.set('camera', 'ndi_source', 'STUDIO (OBS)')
    assert build_card('loading', cfg, 'opening_camera').step[0].startswith('Connecting to NDI')
    nd = build_card('no_camera', cfg)
    assert nd.title == 'NDI source not found' and [a for _, a in nd.actions] == ['restart', 'use_camera']


# ---------------------------------------------------------------------------
# On the stage
# ---------------------------------------------------------------------------
def test_stopped_card_on_a_fresh_window(window):
    assert card(window).key == 'stopped'
    assert window.faults.overlay.isVisibleTo(window.pane('live').stage)


def test_cards_follow_engine_signals(window):
    drive(window, 'starting')
    assert card(window).key == 'loading'
    drive(window, phase='loading_model')
    assert window.faults.overlay.findChild(QLabel, 'faultStep').text() == 'holistic_landmarker.task'
    drive(window, 'running', 'ready')
    assert card(window) is None and not window.faults.overlay.isVisible()
    drive(window, phase='reconnecting')
    assert card(window).key == 'reconnecting'
    drive(window, phase='ready')
    assert card(window) is None
    drive(window, phase='no_camera', reason='Camera 0 could not be opened')
    drive(window, 'stopped', 'stopped')
    assert card(window).key == 'no_camera'


def test_actions(window, monkeypatch):
    calls = []
    eng = window.engine
    monkeypatch.setattr(eng, 'start', lambda: calls.append('start'))
    monkeypatch.setattr(eng, 'stop', lambda: calls.append('stop'))
    monkeypatch.setattr(eng, 'restart', lambda: calls.append('restart'))
    shown = []
    monkeypatch.setattr(window, 'show_pane', lambda *a: shown.append(a))
    opened = []
    monkeypatch.setattr(faults.QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)

    ov = window.faults.overlay
    ov.buttons['start'].click()
    drive(window, phase='reconnecting')
    ov.buttons['stop'].click()
    drive(window, phase='no_camera')
    ov.buttons['restart'].click()
    ov.buttons['use_ndi'].click()
    drive(window, phase='permission_denied')
    ov.buttons['open_settings'].click()
    ov.buttons['restart'].click()
    assert calls == ['start', 'stop', 'restart', 'restart']
    assert shown == [('input', 'camera.use_ndi')]
    assert opened == ['x-apple.systempreferences:com.apple.preference.security?Privacy_Camera']


def test_learn_more_opens_the_troubleshooting_anchor(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window, 'show_pane', lambda *a: shown.append(a))
    for phase, slug in (('reconnecting', 'reconnecting'), ('no_camera', 'camera-not-found'),
                        ('permission_denied', 'camera-access-needed')):
        drive(window, phase=phase)
        window.faults.overlay.link.click()
        assert shown[-1] == ('help', 'troubleshooting#' + slug)
    drive(window, 'starting')
    window.faults.overlay.link.click()
    assert shown[-1] == ('help', 'troubleshooting#starting')
    drive(window, 'stopped', 'stopped')
    # the sticky permission fault was cleared by the new start; a plain stop shows Stopped
    window.faults.overlay.link.click()
    assert shown[-1] == ('help', 'troubleshooting#stopped')


def test_toolbar_stays_clickable_under_a_card(window):
    # The card lives inside the stage: it must not use the window-wide overlay host
    assert window.faults.overlay.parentWidget() is window.pane('live').stage
    assert not window.overlay_host().isVisible()
