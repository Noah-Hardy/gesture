"""Tests for the launch animation (#74): cues, easing, choreography, play rules."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src import osc_protocol
from src.ui import launch
from src.ui.launch import CUES, EASING, JOINTS, POSE_ICON, TOTAL, LaunchAnimation


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class _Config:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def get(self, section, key=None, default=None):
        assert (section, key) == ('ui', 'launch_animation')
        return self.enabled


def test_cues_derived_from_scenes():
    assert CUES == {'Sensing': 0, 'Locking': 1.2, 'Dance': 2.2, 'Collapse': 3.6, 'Reveal': 5.0}
    assert TOTAL == 7.5


def test_duplicate_scene_binds_to_first():
    cues, total = launch.derive_cues((('A', 1), ('B', 2), ('A', 3)))
    assert cues == {'A': 0, 'B': 1} and total == 6


def test_easing_values():
    for name in ('linear', 'easeInOutQuad', 'easeOutCubic', 'easeOutBack'):
        assert EASING[name](0) == pytest.approx(0)
        assert EASING[name](1) == pytest.approx(1)
    assert EASING['easeInOutQuad'](0.25) == pytest.approx(0.125)
    assert EASING['easeOutCubic'](0.5) == pytest.approx(0.875)
    assert EASING['easeOutBack'](0.5) == pytest.approx(1.0876975)
    assert max(EASING['easeOutBack'](i / 100) for i in range(101)) > 1.09


def test_animate_clamps_outside_range():
    f = launch.animate(10, 20, 1, 2, EASING['linear'])
    assert (f(0), f(1), f(1.5), f(2), f(9)) == (10, 10, 15, 20, 20)


def test_pose_endpoints():
    assert launch.pose_at(0) == launch.POSE_IDLE
    assert launch.pose_at(CUES['Dance']) == launch.POSE_IDLE
    end = launch.pose_at(CUES['Collapse'])
    for j in JOINTS:
        assert end[j] == pytest.approx(POSE_ICON[j])
    assert launch.pose_at(TOTAL)['rf'] == pytest.approx(POSE_ICON['rf'])
    mid = launch.pose_at(CUES['Dance'] + 0.28 * 1.4)
    assert mid['lh'] == pytest.approx(launch.POSE_A['lh'])


def test_lock_order_and_node_pop():
    assert launch.lock_time('head') == pytest.approx(0.45)
    assert launch.lock_time('chest') - launch.lock_time('head') == pytest.approx(0.105)
    assert launch.node_state(0.2, 'head')['opacity'] == 0
    s = launch.node_state(launch.lock_time('head') + 0.05, 'head')
    assert s['flash'] and s['opacity'] > 0
    assert launch.node_state(2.0, 'head')['radius'] == pytest.approx(launch.NODE_R['head'])


def test_bone_waits_for_both_joints():
    assert launch.bone_progress(launch.lock_time('chest'), 'head', 'chest') == 0
    assert launch.bone_progress(TOTAL, 'head', 'chest') == 1


def test_collapse_lands_at_icon_scale():
    f = launch.figure_state(CUES['Collapse'] + 0.55)
    assert f['fig_scale'] == pytest.approx(1.04 * 0.385)
    assert f['cy'] == pytest.approx(452)


def test_reveal_and_flight():
    assert launch.window_state(CUES['Reveal'])['shell'] == 0
    w = launch.window_state(TOTAL)
    assert all(w[k] == pytest.approx(1) for k in ('shell', 'rail', 'stage', 'meters', 'osc', 'cons', 'shell_s'))
    i = launch.icon_state(TOTAL)
    assert (i['fly_x'], i['fly_y'], i['fly_s']) == pytest.approx((216, 225, 0.105))
    assert i['word_a'] == 0


def test_stream_uses_real_addresses():
    f = osc_protocol.make_formatter('float')
    assert f.POSE_TRACKING_ADDRESS in launch.ADDRS and f.HAND_TRACKING_ADDRESS in launch.ADDRS
    assert len(set(launch.ADDRS)) == 6
    for a in launch.ADDRS:
        assert not a.startswith(('/pose/bounds', '/status', '/hand/'))


def test_should_play_rules(monkeypatch):
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: False)
    assert LaunchAnimation.should_play(_Config(True), 2000)
    assert not LaunchAnimation.should_play(_Config(False), 2000)
    # Plays on every launch, however fast the window was ready (#74)
    assert LaunchAnimation.should_play(_Config(True), 800)
    assert LaunchAnimation.should_play(_Config(True), 120)
    assert LaunchAnimation.should_play(_Config(True))
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: True)
    assert not LaunchAnimation.should_play(_Config(True), 5000)


def test_reduce_motion_never_raises():
    assert isinstance(launch.reduce_motion_enabled(), bool)


def test_skip_finishes_immediately(app):
    anim = LaunchAnimation()
    calls = []
    anim.play(lambda: calls.append(1))
    assert anim.is_playing
    anim.skip()
    anim.skip()
    assert calls == [1] and not anim.is_playing and not anim.isVisible()


def test_click_skips(app):
    anim = LaunchAnimation()
    calls = []
    anim.play(lambda: calls.append(1))
    QTest.mouseClick(anim, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert calls == []  # a stray click right at launch is ignored
    QTest.qWait(launch.SKIP_GRACE_MS + 50)
    QTest.mouseClick(anim, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert calls == [1]


def test_maybe_play_shows_window_when_skipped(app, monkeypatch):
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: False)
    win = QWidget()
    assert launch.maybe_play(win, _Config(False), 5000) is None
    assert win.isVisible()
    win.close()


def test_maybe_play_plays_then_shows_window(app, monkeypatch):
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: False)
    win = QWidget()
    anim = launch.maybe_play(win, _Config(True), 5000)
    assert anim is not None and anim.is_playing and not win.isVisible()
    anim.skip()
    assert win.isVisible()
    win.close()


def test_frame_renders(app):
    img = launch.render_frame(CUES['Reveal'] + 1.0, 480, 270)
    assert (img.width(), img.height()) == (480, 270)


def test_splash_matches_a_positioned_window(app):
    win = QWidget()
    win.resize(1200, 700)
    win.move(100, 80)
    anim = LaunchAnimation(win)
    avail = QApplication.primaryScreen().availableGeometry()
    scale = min(1200 / launch.WIN_W, 700 / launch.WIN_H, avail.width() / launch.CANVAS_W,
                avail.height() / launch.CANVAS_H)
    assert anim.width() == pytest.approx(launch.CANVAS_W * scale, abs=2)
    assert anim.height() == pytest.approx(launch.CANVAS_H * scale, abs=2)
    if scale == 1200 / launch.WIN_W:  # not capped by a small screen: it sits over the window
        assert anim.geometry().center().x() == pytest.approx(100 + 600, abs=2)
        assert anim.geometry().center().y() == pytest.approx(80 + 350, abs=2)


def test_unplaced_window_opens_centred_on_the_splash(app, monkeypatch):
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: False)
    win = QWidget()
    win.setFixedSize(900, 560)
    anim = launch.maybe_play(win, _Config(True), 5000)
    center = anim.geometry().center()
    anim.skip()
    assert win.geometry().center().x() == pytest.approx(center.x(), abs=2)
    assert win.geometry().center().y() == pytest.approx(center.y(), abs=2)
    win.close()


def test_stream_addresses_are_what_float_sends_in_all_mode():
    from types import SimpleNamespace

    class Sender:
        def __init__(self):
            self.packets = []

        def send_packet(self, packet):
            self.packets.append(packet)

    def lms(n):
        return [SimpleNamespace(x=0.1, y=0.2, z=0.3, visibility=0.9) for _ in range(n)]

    sender = Sender()
    emitter = osc_protocol.OscEmitter(sender, 'float')
    emitter.begin_frame()
    emitter.pose(0, lms(33), lms(33))
    emitter.hand('Left', lms(21), lms(21))
    emitter.hand('Right', lms(21), lms(21))
    emitter.pose_status(1)
    emitter.hand_status(2)
    emitter.end_frame()
    wire = b''.join(p.dgram for p in sender.packets)
    for addr in launch.ADDRS:
        assert addr.encode() in wire, addr


def test_launch_animation_toggle_is_app_only():
    from src import live_keys
    assert live_keys.is_app_only('ui.launch_animation')
    assert not live_keys.is_app_only('ui.mode')


def test_app_support_dir_override(tmp_path, monkeypatch):
    from src.config import app_support_dir
    target = tmp_path / 'support'
    monkeypatch.setenv('GESTURE_APP_SUPPORT_DIR', str(target))
    assert app_support_dir() == str(target) and target.is_dir()


def test_tcc_protected_folders_are_not_swept(monkeypatch):
    import os
    from src import updater
    home = os.path.expanduser('~')
    assert updater._is_tcc_protected(os.path.join(home, 'Documents', 'x'))
    assert updater._is_tcc_protected(os.path.join(home, 'Desktop'))
    assert not updater._is_tcc_protected('/Applications')


def test_reveal_lands_on_the_real_window(app, monkeypatch):
    """The splash paints the real window's capture where the window will open."""
    from src.config import Config
    from src.ui.main_window import MainWindow
    win = MainWindow(Config(os.path.join(os.environ.get('TMPDIR', '/tmp'), 'launch_reveal_cfg.json')))
    anim = LaunchAnimation(win)
    plan = anim._build_plan()
    assert plan is not None and plan.pixmap.width() > 0
    assert any(k == 'stage' for k, _ in plan.pieces)
    img = launch.render_frame(CUES['Reveal'] + 1.2, 480, 270)
    assert img.width() == 480
    anim._plan = plan
    anim._t = TOTAL
    assert not anim.grab().isNull()
    anim.close()
    win.close()
