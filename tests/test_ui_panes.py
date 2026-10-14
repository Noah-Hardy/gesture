"""Settings panes (#68): spec rows round-trip through the EditModel to config.json. Run offscreen."""

import json
import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.panes import _sources  # noqa: E402
from src.ui.panes._render import bgr_to_hex, hex_to_bgr  # noqa: E402
from src.ui.panes.spec import PANES  # noqa: E402

PANE_KEYS = tuple(PANES)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(_sources, 'camera_names', lambda: ['Built-in Camera', 'Virtual Camera'])
    monkeypatch.setattr(_sources, 'camera_authorization', lambda: 'authorized')
    monkeypatch.setattr(_sources, 'ndi_sources', lambda: ['STUDIO (OBS)'])
    monkeypatch.setattr(_sources, 'load_costs', lambda: {'lite': 7.2, 'full': 13.0})
    app = QApplication.instance() or QApplication([])
    tokens.apply(app)
    w = MainWindow(Config(str(tmp_path / 'config.json')))
    w._save_timer.stop()
    w.show()
    app.processEvents()
    yield w
    w.engine.stop_and_wait()
    w.close()
    app.processEvents()


def _new_value(b, saved):
    """A valid value different from the saved one, in edit form."""
    row = b.row
    k = row.kind
    if k == 'toggle':
        return not saved
    if k == 'choice':
        options = [o[0] if isinstance(o, tuple) else o for o in row.options]
        return next(o for o in options if o != saved)
    if k == 'select':
        return 1 if row.source == 'camera_devices' else 'STUDIO (OBS)'
    if k == 'num':
        lo, hi = row.limits[0], row.limits[1]
        return str(lo if str(lo) != saved else hi)
    if k == 'decimal':
        return '0.8' if saved != '0.8' else '0.6'
    if k == 'text':
        return '192.168.1.20'
    if k == 'range':
        return 0.25
    if k == 'color':
        return '#112233'
    raise AssertionError(k)


def _all_bound(window):
    for key in PANE_KEYS:
        yield from window.pane(key).bound.values()


def test_every_editable_row_is_registered_with_a_config_key(window):
    keys = {b.row.key for b in _all_bound(window)}
    expected = {r.key for p in PANES.values() for r in p.rows if r.key}
    assert keys == expected
    for k in ('performance.force_gpu', 'camera.ndi_bandwidth', 'camera.processing_width',
              'camera.reconnect_timeout', 'performance.max_pending_frames', 'osc.tracking_hold',
              'osc.protocol', 'display.draw_landmarks', 'display.render_in_background'):
        assert k in keys
    assert 'performance.gc_interval' not in keys and 'performance.force_legacy' not in keys


def test_every_row_round_trips_to_config_json(window):
    edits = window.edits
    expected = {}
    for b in _all_bound(window):
        saved = edits.saved(b.fid)
        value = _new_value(b, saved)
        edits.edit(b.fid, value)
        assert edits.is_dirty(b.fid) and edits.validate(b.fid) is None, (b.fid, value)
        f = edits.field(b.fid)
        expected[(f.section, f.key)] = f.encode(value)
    assert edits.apply() is not None
    data = json.load(open(window.config.config_file))
    for (section, key), want in expected.items():
        assert data[section][key] == want, (section, key)
    assert Config(window.config.config_file).get('performance', 'force_gpu') is True
    assert not edits.is_dirty()


def test_controls_follow_the_model_and_edit_it(window):
    edits = window.edits
    inp, osc = window.pane('input'), window.pane('osc')
    b = inp.bound['input.camera.width']
    b.control.setText('1280')
    assert edits.value('input.camera.width') == '1280' and edits.is_dirty('input.camera.width')
    assert b.field_row.is_dirty() and b.field_row.chip_visible()
    edits.revert()
    assert b.control.text() == '640' and not b.field_row.is_dirty()

    proto = osc.bound['osc.osc.protocol']
    proto.control.setCurrent('JSON')
    assert edits.value('osc.osc.protocol') == 'json'
    tgl = window.pane('advanced').bound['advanced.performance.show_fps']
    tgl.control.setChecked(True)
    assert edits.value('advanced.performance.show_fps') is True
    edits.revert()
    assert tgl.control.isChecked() is False and proto.control.current() == 'Float'

    sl = window.pane('tracking').bound['tracking.mediapipe.min_detection_confidence']
    sl.control.setValue(0.3)
    assert edits.value('tracking.mediapipe.min_detection_confidence') == 0.3


def test_invalid_row_shows_its_error(window):
    b = window.pane('osc').bound['osc.osc.port']
    b.control.setText('0')
    assert not b.field_row._error.isHidden()
    assert 'Ports run 1' in b.field_row._error.text()
    assert not window.pending_bar.apply_button.isEnabled()


def test_restart_rows_show_the_chip_when_dirty(window):
    edits = window.edits
    for b in _all_bound(window):
        edits.edit(b.fid, _new_value(b, edits.saved(b.fid)))
        assert b.field_row.chip_visible() == b.row.restart, b.fid
        edits.revert()
        assert not b.field_row.chip_visible()
    restart = {b.row.key for b in _all_bound(window) if b.row.restart}
    assert {'osc.protocol', 'ui.mode', 'mediapipe.pose_model_type', 'mediapipe.num_poses',
            'hand.num_hands', 'performance.force_cpu', 'performance.force_gpu',
            'performance.no_holistic'} <= restart
    assert {k for k in restart if k.startswith('camera.')} == {
        b.row.key for b in window.pane('input').bound.values()}
    assert 'osc.host' not in restart and 'osc.port' not in restart


def test_address_map_follows_the_pending_format_and_mode(window):
    osc = window.pane('osc')
    window.show_pane('osc')
    osc.refresh_map()
    rows = [r[0] for r in osc._map_holder.table_rows()[1:]]
    assert '/pose/lm/*' in rows and '/pose/raw' not in rows
    window.edits.edit('osc.osc.protocol', 'legacy')
    window.edits.edit('osc.ui.mode', 'hand')
    rows = [r[0] for r in osc._map_holder.table_rows()[1:]]
    assert '/left_hand/raw' in rows and '/pose/raw' not in rows and '/mp/heartbeat' in rows
    assert osc._sample_block.text().startswith('/left_hand/raw')
    window.edits.revert()
    assert '/pose/lm/*' in [r[0] for r in osc._map_holder.table_rows()[1:]]


def test_device_lists_and_permission(window):
    app = QApplication.instance()
    inp = window.pane('input')
    window.show_pane('input')
    for _ in range(200):
        app.processEvents()
        if inp._camera_names is not None and inp._ndi_sources is not None:
            break
        time.sleep(0.01)
    cam = inp.bound['input.camera.device_id']
    assert cam.labels == ['Camera 0 — Built-in Camera', 'Camera 1 — Virtual Camera']
    ndi = inp.bound['input.camera.ndi_source']
    assert ndi.values == ['', 'STUDIO (OBS)']
    assert inp._permission._label.text() == 'Granted'
    cam.control.setCurrentText(cam.labels[1])
    assert window.edits.value('input.camera.device_id') == 1


def test_frame_cost_table_uses_the_cache(window):
    tr = window.pane('tracking')
    tr.refresh_live_data()
    rows = tr._cost_holder.table_rows()
    assert rows[1][2] == '7 ms' and rows[3][2] == '—'


def test_scroll_to_anchor_and_colours(window):
    window.show_pane('input', 'camera.width')
    window.show_pane('advanced', 'osc.tracking_hold')
    assert bgr_to_hex([66, 117, 245]) == '#F57542'
    assert hex_to_bgr('#F57542') == [66, 117, 245]
    window.pane('preview').bound['preview.display.landmark_color'].control.colorChanged.emit('#102030')
    assert window.edits.value('preview.display.landmark_color') == '#102030'
