"""App shell tests (#63). Run offscreen; skipped when PySide6 cannot import."""

import json
import os
import sys

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.engine import EngineController, build_argv  # noqa: E402
from src.ui.main_window import MainWindow, layout_mode_for  # noqa: E402
from src.ui.panes import KEYS  # noqa: E402

ACTION_KEYS = (
    'start_stop', 'restart', 'apply', 'revert', 'settings', 'toggle_console', 'toggle_osc',
    'next_pane', 'prev_pane', 'reveal_config', 'copy_console', 'docs', 'check_updates',
    'about', 'setup', 'quit',
)


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / 'config.json')


@pytest.fixture
def window(app, config_path):
    w = MainWindow(Config(config_path))
    w.show()
    app.processEvents()
    yield w
    w._save_timer.stop()
    w.engine.stop_and_wait()
    w.hide()
    w.deleteLater()


def _resize(app, w, width, height=816):
    w.resize(width, height)
    app.processEvents()
    app.processEvents()


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------
def test_window_builds_with_default_and_minimum_size(window):
    assert (window.width(), window.height()) == (1240, 816)
    assert (window.minimumWidth(), window.minimumHeight()) == (980, 620)
    assert window.toolbar.height() == 76
    assert window.rail.width() == 196
    assert window.edits is not None


def test_layout_mode_thresholds():
    assert layout_mode_for(1400) == layout_mode_for(1180) == 'comfortable'
    assert layout_mode_for(1179) == layout_mode_for(1024) == 'narrow'
    assert layout_mode_for(1023) == layout_mode_for(980) == 'minimum'


def test_breakpoints_fire_in_order_when_shrinking(app, window):
    seen = []
    window.layoutModeChanged.connect(seen.append)
    _resize(app, window, 1400)
    for width in range(1400, 979, -20):
        _resize(app, window, width)
    assert seen == ['narrow', 'minimum']
    assert window.layout_mode == 'minimum'
    assert window.rail.width() == 52 and window.rail.is_collapsed()
    assert not window.console_open()
    seen.clear()
    for width in range(980, 1401, 20):
        _resize(app, window, width)
    assert seen == ['narrow', 'comfortable']
    assert window.rail.width() == 196
    assert window.console_open()      # restored: the close was automatic, not a choice


def test_run_button_and_stats_do_not_move(app, window):
    def anchors():
        run = window.toolbar.run
        stats = window.toolbar.stats
        return (run.mapTo(window, run.rect().topLeft()).x(),
                window.width() - stats.mapTo(window, stats.rect().topRight()).x(),
                stats.width())
    base = None
    for width in (1400, 1180, 1100, 1024, 980):
        _resize(app, window, width)
        if base is None:
            base = anchors()
        assert anchors() == base, width
    # neither the chip nor the readout text may shift them
    window.set_restart_required(True)
    window.set_stats(120, 345)
    app.processEvents()
    assert anchors() == base


def test_long_readouts_elide_instead_of_pushing_the_toolbar(app, window):
    _resize(app, window, 980)
    window.set_restart_required(True)
    run = window.toolbar.run
    stats = window.toolbar.stats
    before = (run.mapTo(window, run.rect().topLeft()).x(),
              window.width() - stats.mapTo(window, stats.rect().topRight()).x())
    window.config.set('camera', 'use_ndi', True)
    window.config.set('camera', 'ndi_source', 'STUDIO-MAC (OBS Virtual Camera and Friends)')
    window.config.set('osc', 'host', 'a-very-long-hostname.example.studio.internal')
    window.refresh_readouts()
    app.processEvents()
    after = (run.mapTo(window, run.rect().topLeft()).x(),
             window.width() - stats.mapTo(window, stats.rect().topRight()).x())
    assert after == before
    assert stats.mapTo(window, stats.rect().topLeft()).x() >= window.toolbar.chip.geometry().right()


def test_restart_chip_and_stats_text(window):
    assert window.toolbar.chip.isHidden()
    window.set_restart_required(True)
    assert not window.toolbar.chip.isHidden()
    window.set_stats(29.4, 11.2)
    assert window.toolbar.stats.text() == '29 fps · 11 ms'
    window.set_stats(None, None)
    assert window.toolbar.stats.text() == '-- fps · -- ms'


def test_readouts_come_from_config(app, config_path):
    cfg = Config(config_path)
    cfg.set('ui', 'mode', 'pose')
    cfg.set('camera', 'use_ndi', True)
    cfg.set('camera', 'ndi_source', 'STUDIO (OBS)')
    cfg.set('osc', 'host', '10.0.0.2')
    cfg.set('osc', 'port', 9000)
    w = MainWindow(cfg)
    text = ' '.join(r.value() for r in w.toolbar.readouts.values())
    assert 'pose' in text and 'STUDIO (OBS)' in text and '10.0.0.2:9000' in text
    w._save_timer.stop()


def test_update_card_slot(window):
    class Rel:
        version = '0.5.0'
    assert window.rail.update_card.isHidden()
    window.set_update_available(Rel())
    assert not window.rail.update_card.isHidden()
    window.set_update_available(None)
    assert window.rail.update_card.isHidden()


# ---------------------------------------------------------------------------
# Panes and actions
# ---------------------------------------------------------------------------
def test_pane_keys_in_rail_order():
    assert KEYS == ('live', 'input', 'osc', 'tracking', 'preview', 'advanced', 'help')


def test_panes_switch(window):
    seen = []
    window.paneChanged.connect(seen.append)
    for key in KEYS:
        window.show_pane(key)
        assert window.current_pane() == key
        assert window.stack.currentWidget() is window.pane(key)
        assert window.rail.items[key].is_active()
    assert seen == list(KEYS[1:])


def test_next_prev_pane_wrap(window):
    window.show_pane('help')
    window.actions['next_pane'].trigger()
    assert window.current_pane() == 'live'
    window.actions['prev_pane'].trigger()
    assert window.current_pane() == 'help'
    window.actions['settings'].trigger()
    assert window.current_pane() == 'input'


def test_every_action_key_exists(window):
    for key in ACTION_KEYS:
        assert key in window.actions, key
    assert window.actions['start_stop'].shortcut().toString() == 'Ctrl+Return'
    assert window.actions['toggle_console'].shortcut().toString() == 'Ctrl+Shift+C'


def test_pending_bar_slot(window):
    from PySide6.QtWidgets import QLabel
    assert window._pending_slot.isHidden()
    bar = QLabel('pending')
    window.set_pending_bar(bar)
    assert not window._pending_slot.isHidden()
    window.set_pending_bar(None)
    assert window._pending_slot.isHidden()


def test_overlay_host_dims_only_while_a_card_is_shown(app, window):
    from PySide6.QtWidgets import QLabel
    host = window.overlay_host()
    window.first_run.wizard.hide()  # a fresh config opens the first-run wizard (#69)
    app.processEvents()
    assert host.isHidden()
    card = QLabel('card', host)
    card.show()
    app.processEvents()
    assert host.isVisible()
    card.hide()
    app.processEvents()
    assert host.isHidden()


def test_toast_shows_then_hides(app, window):
    window.toast('Saved', ms=30)
    assert window._toast.isVisible()
    from PySide6.QtTest import QTest
    QTest.qWait(120)
    assert window._toast.isHidden()


def test_copy_console(app, window):
    window.console.append_line('hello engine')
    window.actions['copy_console'].trigger()
    assert 'hello engine' in QApplication.clipboard().text()


# ---------------------------------------------------------------------------
# Console drawer
# ---------------------------------------------------------------------------
def test_console_header_and_toggle(window):
    assert window.console_open()
    window.toggle_console()
    assert not window.console_open()
    window.actions['toggle_console'].trigger()
    assert window.console_open()


def test_console_drawer_round_trips_through_config(app, config_path):
    w = MainWindow(Config(config_path))
    w.show()
    app.processEvents()
    w.set_console_height(220)
    w.set_console_open(False)
    w.close()
    saved = json.load(open(config_path))['ui']
    assert saved['console_height'] == 220 and saved['console_open'] is False

    w2 = MainWindow(Config(config_path))
    w2.show()
    app.processEvents()
    assert w2.console_height() == 220
    assert not w2.console_open()
    w2.set_console_open(True)
    app.processEvents()
    assert w2.splitter.sizes()[1] == 220
    w2._save_timer.stop()


def test_console_height_is_clamped(window):
    window.set_console_height(10)
    assert window.console_height() == 90
    window.set_console_height(9999)
    assert window.console_height() == 360


def test_console_overlays_instead_of_shrinking_stage_below_240(app, window):
    window.set_console_height(360)
    _resize(app, window, 1240, 620)
    assert window.splitter.sizes()[1] == 360               # the drawer keeps its height...
    assert window.stack.height() >= 240                    # ...and the pane is not squeezed
    assert window.stack.y() == 0
    stage = window._stage
    assert stage.height() < 240                            # the drawer covers the overflow
    window.set_console_height(160)
    _resize(app, window, 1240, 816)
    assert window.stack.height() == window._stage.height()


def test_pending_bar_stays_above_the_console(app, window):
    from PySide6.QtWidgets import QLabel
    bar = QLabel('pending')
    bar.setFixedHeight(40)
    window.set_pending_bar(bar)
    window.set_console_height(360)
    _resize(app, window, 1240, 620)
    stage = window._stage
    assert bar.mapTo(stage, bar.rect().bottomLeft()).y() + 1 == stage.height()


def test_console_open_state_and_height_apply_on_relaunch(app, config_path):
    w = MainWindow(Config(config_path))
    w.show()
    app.processEvents()
    w.set_console_height(220)
    w.close()
    w2 = MainWindow(Config(config_path))
    w2.show()
    app.processEvents()
    assert w2.console_open()
    assert w2.splitter.sizes()[1] == 220
    w2._save_timer.stop()


def test_section_open_keys_are_retired(config_path):
    with open(config_path, 'w') as f:
        json.dump({'ui': {'input_section_open': False, 'log_section_open': False,
                          'osc_section_open': True, 'model_section_open': True, 'mode': 'pose'}}, f)
    cfg = Config(config_path)
    ui = cfg.get('ui')
    assert not [k for k in ui if k.endswith('_section_open')]
    assert ui['mode'] == 'pose'


# ---------------------------------------------------------------------------
# Engine controller
# ---------------------------------------------------------------------------
def test_argv_from_default_config(config_path):
    cfg = Config(config_path)
    argv = build_argv(cfg)
    assert argv[0] == sys.executable
    assert argv[1].endswith('main.py')
    tail = argv[2:]
    assert tail[0] == 'all'
    assert tail[tail.index('--host') + 1] == '127.0.0.1'
    assert tail[tail.index('--port') + 1] == '1234'
    assert tail[tail.index('--camera') + 1] == '0'
    assert tail[tail.index('--pose-model') + 1] == 'lite'
    assert '--no-mirror' in tail and '--preview' not in tail and '--no-preview' not in tail
    assert '--fps-cap' not in tail and '--ndi' not in tail and '--force-cpu' not in tail


def test_argv_reflects_config(config_path):
    cfg = Config(config_path)
    cfg.set('ui', 'mode', 'hand')
    cfg.set('osc', 'host', '192.168.1.5')
    cfg.set('osc', 'port', 7000)
    cfg.set('camera', 'use_ndi', True)
    cfg.set('camera', 'ndi_source', 'CAM (OBS)')
    cfg.set('mediapipe', 'pose_model_type', 'heavy')
    cfg.set('performance', 'target_fps', 30)
    cfg.set('performance', 'show_fps', True)
    cfg.set('performance', 'force_cpu', True)
    cfg.set('performance', 'force_gpu', True)
    cfg.set('performance', 'no_holistic', True)
    cfg.set('display', 'mirror_preview', True)
    tail = build_argv(cfg)[2:]
    assert tail[0] == 'hand'
    assert tail[tail.index('--host') + 1] == '192.168.1.5'
    assert tail[tail.index('--port') + 1] == '7000'
    assert tail[tail.index('--ndi-source') + 1] == 'CAM (OBS)'
    assert '--ndi' in tail and '--camera' not in tail
    assert tail[tail.index('--pose-model') + 1] == 'heavy'
    assert tail[tail.index('--fps-cap') + 1] == '30'
    assert '--fps' in tail and '--mirror' in tail
    assert '--force-cpu' in tail and '--force-gpu' not in tail and '--no-holistic' in tail


def test_frozen_argv_is_the_executable_alone(config_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    argv = build_argv(Config(config_path))
    assert argv[0] == sys.executable and argv[1] == 'all'


def test_engine_start_refuses_a_bad_port(app, config_path):
    cfg = Config(config_path)
    cfg.set('osc', 'port', 99999)
    ctl = EngineController(cfg)
    lines = []
    ctl.output.connect(lines.append)
    ctl.start()
    assert not ctl.is_running() and ctl.state == 'stopped'
    assert any('Invalid OSC port' in ln for ln in lines)
