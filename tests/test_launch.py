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
    assert not LaunchAnimation.should_play(_Config(True), 800)
    assert not LaunchAnimation.should_play(_Config(True), 120)
    assert LaunchAnimation.should_play(_Config(True), 801)
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
