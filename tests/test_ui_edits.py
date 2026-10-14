"""Pending-change model and bar (#64). Run offscreen."""

import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.edits import validate_host, validate_number  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.pending_bar import BG_INVALID, BG_LIVE, BG_RESTART  # noqa: E402


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    tokens.apply(app)
    w = MainWindow(Config(str(tmp_path / 'config.json')))
    e = w.edits
    e.register('t_osc', 'OSC Output', 'Host', 'osc.host', kind='text', host=True)
    e.register('t_osc', 'OSC Output', 'Port', 'osc.port', kind='num', limits=(1, 65535, 'Ports run 1–65535; anything under 1024 needs root'))
    e.register('t_input', 'Input', 'Capture width', 'camera.width', kind='num', restart=True, limits=(160, 3840))
    e.register('t_adv', 'Advanced', 'Target FPS', 'performance.target_fps', kind='num', limits=(0, 240))
    w._save_timer.stop()
    yield w
    w.engine.stop_and_wait()
    w.close()
    app.processEvents()


def test_validators():
    assert validate_host('127.0.0.1') is None
    assert validate_host('localhost') is None
    assert validate_host('studio.local') is None
    assert validate_host('300.1.1.1') == 'Each part of an IPv4 address is 0–255'
    assert validate_host('bad host') == 'Not a valid IPv4 address or hostname'
    assert validate_number('', (1, 5)) == 'Cannot be empty'
    assert validate_number('1.5', (1, 5)) == 'Whole numbers only'
    assert validate_number('9', (1, 5)) == 'Must be between 1 and 5'
    assert validate_number('3', (1, 5)) is None


def test_bar_counts_and_names(window):
    e = window.edits
    assert not e.is_dirty()
    e.edit('t_osc.osc.port', '9000')
    e.edit('t_osc.osc.host', '10.0.0.5')
    e.edit('t_input.camera.width', '1280')
    e.edit('t_adv.performance.target_fps', '30')
    bar = window.pending_bar
    assert bar.summary.text() == '4 changes pending · 1 needs a restart'
    assert bar.detail.text() == ('OSC Output · Host   OSC Output · Port   Input · Capture width   +1 more')
    assert bar.bg_name() == BG_RESTART
    assert not bar.isHidden() or window._pending_slot.isVisibleTo(window)
    assert window.toolbar.chip.isVisibleTo(window.toolbar)
    e.edit('t_input.camera.width', '640')       # back to saved: clean
    assert bar.summary.text() == '3 changes pending · applies live'
    assert bar.bg_name() == BG_LIVE
    assert not window.toolbar.chip.isVisibleTo(window.toolbar)


def test_invalid_disables_apply(window):
    e = window.edits
    e.edit('t_osc.osc.port', '99999')
    bar = window.pending_bar
    assert not bar.apply_button.isEnabled()
    assert bar.bg_name() == BG_INVALID
    assert e.apply() is None
    assert window.config.get('osc', 'port') == 1234
    e.edit('t_osc.osc.port', '1235')
    assert bar.apply_button.isEnabled()


def test_revert_restores(window):
    e = window.edits
    e.edit('t_osc.osc.port', '9000')
    got = []
    e.reverted.connect(lambda: got.append(1))
    e.revert()
    assert got and e.value('t_osc.osc.port') == '1234' and not e.is_dirty()
    assert window._pending_slot.isHidden()


def test_apply_writes_once(window, monkeypatch):
    saves = []
    real = Config.save
    monkeypatch.setattr(Config, 'save', lambda self: (saves.append(1), real(self)))
    e = window.edits
    e.edit('t_osc.osc.port', '9000')
    e.edit('t_input.camera.width', '1280')
    window.actions['apply'].trigger()
    assert len(saves) == 1
    data = json.load(open(window.config.config_file))
    assert data['osc']['port'] == 9000 and data['camera']['width'] == 1280
    assert not e.is_dirty()


def test_restart_class_restarts_running_engine(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.engine, 'is_running', lambda: True)
    monkeypatch.setattr(window.engine, 'restart', lambda: calls.append('restart'))
    monkeypatch.setattr(window.engine, 'set_live', lambda k, v: calls.append((k, v)) or False)
    e = window.edits
    e.edit('t_osc.osc.port', '9000')
    assert e.apply() is False
    assert calls == [('osc.port', 9000)]
    e.edit('t_input.camera.width', '1280')
    assert e.apply() is True
    assert calls[-1] == 'restart'
    monkeypatch.undo()


def test_restart_class_follows_live_keys(window):
    e = window.edits
    gc = e.register('advanced', 'Advanced', 'GC', 'performance.gc_enabled', kind='toggle')
    assert e.field(gc).restart            # not in LIVE_KEYS
    assert not e.field('osc.osc.port').restart
    assert e.field('input.camera.width').restart


def test_external_config_changes_reach_the_model(window):
    e = window.edits
    fid = e.register('preview', 'Preview', 'Mirror', 'display.mirror_preview', kind='toggle')
    seen = []
    e.changed.connect(seen.append)
    window.config.set('display', 'mirror_preview', True)
    e.sync_external()
    assert fid in seen and e.value(fid) is True and not e.is_dirty(fid)
    e.edit(fid, False)
    window.config.set('display', 'mirror_preview', False)
    e.sync_external()
    assert not e.is_dirty(fid)
