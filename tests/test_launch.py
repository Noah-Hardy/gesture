"""Tests for the launch animation (#74): cues, easing, choreography, play rules."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src import osc_protocol
from src.ui import launch
from src.ui.launch import CUES, EASING, JOINTS, POSE_ICON, TOTAL, LaunchOverlay


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
    assert LaunchOverlay.should_play(_Config(True), 2000)
    assert not LaunchOverlay.should_play(_Config(False), 2000)
    # Plays on every launch, however fast the window was ready (#74)
    assert LaunchOverlay.should_play(_Config(True), 800)
    assert LaunchOverlay.should_play(_Config(True), 120)
    assert LaunchOverlay.should_play(_Config(True))
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: True)
    assert not LaunchOverlay.should_play(_Config(True), 5000)


def test_reduce_motion_never_raises():
    assert isinstance(launch.reduce_motion_enabled(), bool)


def _host(app, w=1200, h=760):
    host = QWidget()
    host.resize(w, h)
    host.show()
    return host


def test_overlay_is_a_child_of_the_window_and_covers_it(app):
    host = _host(app)
    anim = LaunchOverlay(host)
    assert anim.parent() is host and anim.geometry() == host.rect()
    host.resize(900, 600)
    app.processEvents()
    assert anim.geometry() == host.rect()  # follows resizes
    host.close()


def test_skip_removes_the_overlay_immediately(app):
    host = _host(app)
    anim = LaunchOverlay(host)
    done = []
    anim.finished.connect(lambda: done.append(1))
    anim.play()
    assert anim.is_playing and anim.isVisible()
    anim.skip()
    anim.skip()
    assert done == [1] and not anim.is_playing and not anim.isVisible()
    host.close()


def test_click_skips_after_the_grace_period(app):
    host = _host(app)
    anim = LaunchOverlay(host)
    anim.play()
    QTest.mouseClick(anim, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert anim.is_playing  # a stray click right at launch is ignored
    QTest.qWait(launch.SKIP_GRACE_MS + 50)
    QTest.mouseClick(anim, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert not anim.is_playing
    host.close()


def test_it_runs_out_at_the_end(app, monkeypatch):
    host = _host(app)
    anim = LaunchOverlay(host)
    anim.play()
    monkeypatch.setattr(launch, 'TOTAL', 0.0)
    anim._tick()
    assert not anim.is_playing and not anim.isVisible()
    host.close()


def test_maybe_play_shows_window_when_skipped(app, monkeypatch):
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: False)
    win = QWidget()
    assert launch.maybe_play(win, _Config(False), 5000) is None
    assert win.isVisible()
    win.close()


def test_maybe_play_shows_the_window_at_once_with_the_overlay_on_top(app, monkeypatch):
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: False)
    win = QWidget()
    anim = launch.maybe_play(win, _Config(True), 5000)
    assert anim is not None and anim.is_playing and win.isVisible()  # one window, shown now
    assert anim.parent() is win
    anim.skip()
    assert win.isVisible()
    win.close()


def test_frame_renders(app):
    img = launch.render_frame(CUES['Reveal'] + 1.0, 480, 270)
    assert (img.width(), img.height()) == (480, 270)


def _window(tmp_path):
    from src.config import Config
    from src.ui.main_window import MainWindow
    cfg = Config(str(tmp_path / 'config.json'))
    cfg.set('ui', 'first_run_complete', True)
    return MainWindow(cfg)


def test_reveal_uncovers_the_real_panes_in_order(app, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, 'reduce_motion_enabled', lambda: False)
    win = _window(tmp_path)
    anim = launch.maybe_play(win, _Config(True))
    regions = {k for k, _ in anim.regions()}
    assert {'rail', 'stage', 'meters', 'osc', 'cons'} <= regions
    r = CUES['Reveal']
    ws = [launch.window_state(r + d) for d in (0.2, 0.5, 0.8, 1.1)]
    for earlier, later in (('rail', 'cons'), ('stage', 'cons')):
        assert ws[0][earlier] >= ws[0][later]
    assert ws[0]['shell'] < ws[-1]['shell'] and ws[-1]['cons'] == 1
    anim._t = r + 0.9  # mid-reveal: paints over the live interface without error
    assert not anim.grab().isNull()
    # the real rail mark waits (invisible) until the icon lands, then returns
    assert win.rail.mark.graphicsEffect() is not None
    anim.skip()
    assert win.rail.mark.graphicsEffect() is None
    assert win.overlay_host().graphicsEffect() is None
    win._save_timer.stop()
    win.engine.stop_and_wait()
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
