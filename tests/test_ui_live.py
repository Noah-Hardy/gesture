"""Live pane tests (#66). Run offscreen; skipped when PySide6 cannot import."""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.osc_activity import ActivityTracker, family_of, format_uptime, predict_families  # noqa: E402


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


@pytest.fixture
def window(app, tmp_path):
    cfg = Config(str(tmp_path / 'config.json'))
    cfg.set('ui', 'first_run_complete', True)
    cfg.set('osc', 'protocol', 'float')
    w = MainWindow(cfg)
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


def status(counts, uptime, **over):
    msg = {'phase': 'ready', 'fps': 30.0, 'latency_ms': 12.0, 'detections': {'pose': 1, 'hands': 2},
           'queue': 1, 'dropped': 0, 'sent': 0, 'per_address': dict(counts), 'uptime': uptime,
           'frame_seq': 1}
    msg.update(over)
    return msg


class Wire:
    """Cumulative per-address counts, advanced one frame at a time at 10 status/s."""

    def __init__(self, hands=True, fps=30):
        self.counts = {}
        self.t = 100.0
        self.hands = hands
        self.per_tick = fps // 10

    def tick(self, **over):
        self.t += 0.1
        for _ in range(self.per_tick):
            for k in range(33):
                self._add('/pose/lm/%d' % k)
                self._add('/pose/world/lm/%d' % k)
            self._add('/gesture/pose/status')
            self._add('/gesture/hand/status')
            if self.hands:
                for side in ('left_hand', 'right_hand'):
                    for k in range(21):
                        self._add('/%s/lm/%d' % (side, k))
        return status(self.counts, self.t, **over)

    def _add(self, a):
        self.counts[a] = self.counts.get(a, 0) + 1


# ---------------------------------------------------------------------------
# Address model
# ---------------------------------------------------------------------------
def test_family_grouping():
    assert family_of('/pose/lm/12') == '/pose/lm/*'
    assert family_of('/left_hand/world/lm/7') == '/left_hand/world/lm/*'
    assert family_of('/pose/1/lm/3') == '/pose/1/lm/*'
    assert family_of('/pose/bounds') == '/pose/bounds'


def test_predicted_rows_float_all():
    rows = predict_families('float', 'all')
    for fam in ('/pose/lm/*', '/pose/world/lm/*', '/left_hand/lm/*', '/right_hand/world/lm/*',
                '/pose/bounds', '/gesture/pose/status', '/gesture/hand/tracking', '/gesture/heartbeat'):
        assert fam in rows
    assert not any(r.endswith('/lm/0') for r in rows)


def test_predicted_rows_follow_mode_and_protocol():
    pose = predict_families('float', 'pose')
    assert '/pose/lm/*' in pose and not any('hand' in r for r in pose)
    hand = predict_families('json', 'hand')
    assert '/left_hand/raw' in hand and not any(r.startswith('/pose') for r in hand)
    legacy = predict_families('legacy', 'all')
    assert '/pose/raw' in legacy and '/mp/heartbeat' in legacy and '/hand/status' in legacy


def test_uptime_format():
    assert format_uptime(3725.9) == '01:02:05'
    assert format_uptime(None) == '00:00:00'


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------
def test_rates_from_cumulative_diffs():
    tr = ActivityTracker(['/pose/lm/*'])
    wire = Wire()
    for _ in range(25):
        tr.feed(wire.tick())
    assert tr.rate('/pose/lm/*') == pytest.approx(33 * 30, rel=0.02)
    assert tr.rate('/gesture/pose/status') == pytest.approx(30, rel=0.05)
    assert tr.total_rate() == pytest.approx((33 * 2 + 42 + 2) * 30, rel=0.02)


def test_unseen_family_is_added_and_restart_resets():
    tr = ActivityTracker([])
    wire = Wire()
    new = tr.feed(wire.tick())
    assert '/pose/lm/*' in new and '/pose/lm/*' in tr.families
    for _ in range(15):
        tr.feed(wire.tick())
    assert tr.rate('/pose/lm/*') > 0
    tr.feed(status({'/pose/lm/0': 1}, 0.2))     # counters went backwards: engine restarted
    assert tr.rate('/pose/lm/*') == 0.0


def test_hand_leaving_silences_only_hand_rows():
    tr = ActivityTracker(predict_families('float', 'all'))
    wire = Wire()
    for _ in range(20):
        tr.feed(wire.tick())
    assert tr.rate('/left_hand/lm/*') > 0
    wire.hands = False
    for _ in range(15):
        tr.feed(wire.tick())
    assert tr.rate('/left_hand/lm/*') == 0.0
    assert tr.rate('/right_hand/lm/*') == 0.0
    # The pose, and the hand status channel (which keeps sending 0), carry on
    assert tr.rate('/pose/lm/*') > 0
    assert tr.rate('/gesture/hand/status') > 0


# ---------------------------------------------------------------------------
# Pane
# ---------------------------------------------------------------------------
def _feed(window, wire, n, **over):
    for _ in range(n):
        window.engine.statusReceived.emit(wire.tick(**over))


def test_live_is_the_default_pane(window):
    assert window.current_pane() == 'live'
    assert window.pane('live').objectName() == 'livePane'


def test_pane_rows_come_from_protocol_and_mode(window):
    pane = window.pane('live')
    assert '/pose/lm/*' in pane.column.rows
    assert '/gesture/heartbeat' in pane.column.rows


def test_status_drives_meters_rows_and_footer(window):
    pane = window.pane('live')
    wire = Wire()
    wire.t = 3700.0
    _feed(window, wire, 25, queue=3)
    assert pane.fps_meter._value == '30'
    assert pane.points_meter._value == '75'
    assert pane.detections_meter._value == '1 pose · 2 hands'
    assert pane.detections_meter._value_px == 17
    assert pane.latency_meter._value == '12'
    assert pane.column.rows['/pose/lm/*'].rate() == pytest.approx(990, rel=0.03)
    assert pane.column.total_label.text().endswith('msg/s')
    assert pane.column.queue_value.text() == '3 / %d' % pane._queue_size()
    assert pane.column.uptime_value.text() == format_uptime(wire.t) == '01:01:42'


def test_hand_leaving_in_the_pane(window):
    pane = window.pane('live')
    wire = Wire()
    _feed(window, wire, 20)
    wire.hands = False
    _feed(window, wire, 15, detections={'pose': 1, 'hands': 0})
    rows = pane.column.rows
    assert rows['/left_hand/lm/*'].rate() == 0.0
    assert rows['/pose/lm/*'].rate() > 0
    assert pane.detections_meter._value == '1 pose · 0 hands'
    assert pane.points_meter._value == '33'


def test_dropped_rising_turns_warn_and_queue_over_75_percent(window):
    pane = window.pane('live')
    warn = tokens.color('warn').name()
    wire = Wire()
    _feed(window, wire, 5)
    assert warn not in pane.column.dropped_value.styleSheet()
    assert warn not in pane.column.queue_value.styleSheet()
    _feed(window, wire, 1, dropped=4)
    assert pane.column.dropped_value.text() == '4'
    assert warn in pane.column.dropped_value.styleSheet()
    size = pane._queue_size()
    _feed(window, wire, 1, dropped=4, queue=size)
    assert warn in pane.column.queue_value.styleSheet()
    _feed(window, wire, 1, dropped=4, queue=1)
    assert warn not in pane.column.queue_value.styleSheet()


def test_stopping_zeroes_everything(window):
    pane = window.pane('live')
    wire = Wire()
    _feed(window, wire, 20)
    window.engine.frameReady.emit(QImage(64, 48, QImage.Format.Format_RGB32))
    assert pane.stage.has_frame()
    window.engine.stateChanged.emit('stopped')
    assert not pane.stage.has_frame()
    assert pane.fps_meter._value == '0'
    assert all(r.rate() == 0 for r in pane.column.rows.values())
    assert pane.column.dropped_value.text() == '0'


def test_stage_chips(window):
    pane = window.pane('live')
    assert pane.stage.source_chip.text() == 'Camera 0 · 640×480 · 30 fps'
    assert pane.stage.holistic_chip.isVisibleTo(pane.stage)
    window.config.set('performance', 'no_holistic', True)
    pane.refresh()
    assert not pane.stage.holistic_chip.isVisibleTo(pane.stage)
    window.config.set('performance', 'no_holistic', False)
    window.config.set('mediapipe', 'num_poses', 2)
    assert not pane.holistic_active()
    window.config.set('mediapipe', 'num_poses', 1)
    window.config.set('ui', 'mode', 'pose')
    assert not pane.holistic_active()
    window.config.set('ui', 'mode', 'all')
    window.config.set('camera', 'use_ndi', True)
    window.config.set('camera', 'ndi_source', 'STUDIO (OBS)')
    pane.refresh()
    assert pane.stage.source_chip.text() == 'STUDIO (OBS)'


def test_stats_overlay_follows_show_fps(window):
    pane = window.pane('live')
    wire = Wire()
    window.config.set('performance', 'show_fps', False)
    _feed(window, wire, 2, fps=29.4)
    assert pane.stage.stats_lines() == []
    window.config.set('performance', 'show_fps', True)
    _feed(window, wire, 1, fps=29.4, latency_ms=11.0)
    assert pane.stage.stats_lines() == ['FPS 29.4', 'POSE 1 · HANDS 2', 'SEND 11 ms']


def test_mirror_chip_writes_config_and_goes_live(window, monkeypatch):
    pane = window.pane('live')
    sent = []
    monkeypatch.setattr(window.engine, 'set_live', lambda k, v: sent.append((k, v)) or True)
    pane.stage.mirror_chip.click()
    assert window.config.get('display', 'mirror_preview') is True
    assert sent == [('display.mirror_preview', True)]
    reread = Config(window.config.config_file)
    assert reread.get('display', 'mirror_preview') is True
    pane.stage.world_chip.click()
    assert pane.world_coords is True
    assert sent == [('display.mirror_preview', True)]      # World coords never touches the engine


def test_frame_is_letterboxed(window, app):
    pane = window.pane('live')
    img = QImage(640, 480, QImage.Format.Format_RGB32)
    img.fill(QColor('white'))
    window.engine.frameReady.emit(img)
    r = pane.stage.frame_rect()
    assert r.width() / r.height() == pytest.approx(640 / 480, rel=0.01)
    assert r.width() <= pane.stage.width() + 0.5 and r.height() <= pane.stage.height() + 0.5


# ---------------------------------------------------------------------------
# Responsive behaviour
# ---------------------------------------------------------------------------
def test_comfortable_docks_the_column(window, app):
    pane = window.pane('live')
    _resize(app, window, 1400)
    assert pane.layout_mode() == 'comfortable'
    assert pane.column.isVisible()
    assert pane.column.width() == 322
    assert not pane.strip.isVisible()
    assert pane._meter_row.isVisible()


def test_narrow_collapses_to_a_strip_and_opens_over_the_stage(window, app):
    pane = window.pane('live')
    _resize(app, window, 1100)
    assert pane.layout_mode() == 'narrow'
    assert not pane.column.isVisible() and pane.strip.isVisible() and pane.strip.width() == 14
    stage_w = pane._left.width()
    pane.strip.clicked()
    app.processEvents()
    assert pane.column.isVisible() and pane.osc_open()
    assert pane._left.width() == stage_w                 # overlays, the stage does not shrink
    pane.set_osc_open(False)
    assert not pane.column.isVisible()
    _resize(app, window, 1400)
    assert pane.column.isVisible()                       # comfortable re-docks it


def test_minimum_moves_meters_into_the_column(window, app):
    pane = window.pane('live')
    _resize(app, window, 980)
    assert pane.layout_mode() == 'minimum'
    assert not pane._meter_row.isVisible()
    assert all(m.parentWidget() is pane.column.meter_slot for m in pane.meters)
    pane.set_osc_open(True)
    app.processEvents()
    assert all(m.isVisible() for m in pane.meters)
    _resize(app, window, 1400)
    assert all(m.parentWidget() is pane._meter_row for m in pane.meters)


def test_toggle_osc_action(window, app):
    pane = window.pane('live')
    app.processEvents()
    _resize(app, window, 1400)
    assert pane.osc_open()
    window.actions['toggle_osc'].trigger()
    assert not pane.osc_open() and pane.strip.isVisible()
    window.actions['toggle_osc'].trigger()
    assert pane.osc_open()


def test_preview_visibility_is_sent_to_the_engine(window, monkeypatch):
    pane = window.pane('live')
    sent = []
    monkeypatch.setattr(window.engine, 'send', lambda cmd: sent.append(cmd))
    pane._visible_sent = None
    window.show_pane('input')
    window.show_pane('live')
    assert sent == [{'cmd': 'visible', 'value': False}, {'cmd': 'visible', 'value': True}]
