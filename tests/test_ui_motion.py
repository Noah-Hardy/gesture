"""Design motion tests: start-up sweep, progress glide, card fades, run-dot breath, meter bars."""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.overlays import faults  # noqa: E402
from src.ui.widgets.display import Meter  # noqa: E402


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


def test_start_up_sweeps_pulses_and_progresses(window):
    eng, ov = window.engine, window.faults.overlay
    eng.stateChanged.emit('starting')
    eng.phaseChanged.emit('opening_camera', '')
    assert ov.isVisible() and ov.sweeping() and window.toolbar.run.pulsing()
    first = ov.sweep_y()
    QTest.qWait(150)
    assert ov.sweep_y() > first  # the band moves down the stage
    bar = ov._bar
    assert bar.fraction() == pytest.approx(0.15)
    eng.phaseChanged.emit('loading_model', '')
    assert ov._bar is bar  # same card, updated in place
    QTest.qWait(150)
    assert 0.15 < bar.fraction() <= 0.55  # gliding, not jumping
    QTest.qWait(400)
    assert bar.fraction() == pytest.approx(0.55)
    eng.stateChanged.emit('running')
    eng.phaseChanged.emit('ready', '')
    assert not ov.isVisible() and not ov.sweeping() and not window.toolbar.run.pulsing()


def test_card_fades_in(window):
    ov = window.faults.overlay
    window.engine.stateChanged.emit('starting')
    assert ov.graphicsEffect() is not None  # fading in
    QTest.qWait(faults.FADE_IN_MS + 150)
    assert ov.graphicsEffect() is None


def test_dismissed_card_leaves_a_fading_ghost(window):
    ov, stage = window.faults.overlay, window.pane('live').stage
    window.engine.stateChanged.emit('starting')
    QTest.qWait(300)
    before = len(stage.children())
    window.engine.stateChanged.emit('running')
    window.engine.phaseChanged.emit('ready', '')
    assert not ov.isVisible()
    assert len(stage.children()) == before + 1  # the ghost
    QTest.qWait(faults.FADE_OUT_MS + 200)
    assert len(stage.children()) == before


def test_meter_bar_glides(app):
    m = Meter('Frame rate', '0', 'fps', 0.0)
    m.resize(150, 84)
    m.show()
    m.set_value('30', 1.0)
    QTest.qWait(120)
    assert 0.0 < m._shown < 1.0
    QTest.qWait(450)
    assert m._shown == pytest.approx(1.0)
    m.close()
