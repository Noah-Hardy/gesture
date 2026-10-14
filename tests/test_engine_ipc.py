"""
Engine <-> launcher IPC (#65): the line protocol, per-address counting, the
shared-memory frame buffer, the engine channel (status, control, exit on socket
close), the launcher-side EngineLink against a fake engine, live-key
validation and application, and the capture-state -> phase mapping.

No engine subprocess and no camera: both ends run in this process over a real
socketpair.
"""
import os
import signal
import socket
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from src import camera_permission, runtime
from src.engine_channel import EngineChannel, NullChannel, exit_on_launcher_loss
from src.engine_link import EngineLink
from src.ipc_protocol import (FrameReader, FrameWriter, LineReader, decode_message, encode_message,
                              make_shm_name, unlink_shm_name)
from src.live_keys import LANDMARKER_KEYS, LIVE_KEYS, coerce_live_value, is_live
from src.live_settings import LiveSettings
from src.osc_protocol import OscEmitter
from tests.golden_harness import CaptureSender, fake_landmarks


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ============================================================================
# LINE PROTOCOL
# ============================================================================
def test_encode_is_one_compact_line():
    line = encode_message({'phase': 'ready', 'fps': 29.5})
    assert line.endswith(b'\n') and line.count(b'\n') == 1
    assert decode_message(line) == {'phase': 'ready', 'fps': 29.5}


def test_decode_rejects_non_objects():
    with pytest.raises(ValueError):
        decode_message(b'[1, 2]')
    with pytest.raises(ValueError):
        decode_message(b'not json')


def test_line_reader_reassembles_split_and_merged_chunks():
    reader = LineReader()
    data = encode_message({'a': 1}) + encode_message({'b': 2}) + encode_message({'c': 3})
    out = []
    for i in range(0, len(data), 3):
        out += reader.feed(data[i:i + 3])
    assert out == [{'a': 1}, {'b': 2}, {'c': 3}]


def test_line_reader_skips_malformed_lines():
    reader = LineReader()
    assert reader.feed(b'garbage\n{"ok": true}\n\n') == [{'ok': True}]


# ============================================================================
# PER-ADDRESS COUNTING
# ============================================================================
def _drive(protocol):
    sender = CaptureSender()
    emitter = OscEmitter(sender, protocol)
    emitter.begin_frame(1.0)
    emitter.pose(0, fake_landmarks(33, 1), fake_landmarks(33, 2, world=True), None, 'pose_0')
    emitter.pose_status(1)
    emitter.hand('Left', fake_landmarks(21, 3), None, None, 'hand_left')
    emitter.hand_status(1)
    emitter.end_frame()
    return emitter, sender


def _addresses(sender):
    from tests.test_osc_protocol import _all_messages
    counts = {}
    for message in _all_messages(sender):
        counts[message.address] = counts.get(message.address, 0) + 1
    return counts


@pytest.mark.parametrize('protocol', ['legacy', 'json', 'float'])
def test_address_counts_match_what_went_on_the_wire(protocol):
    emitter, sender = _drive(protocol)
    assert emitter.address_counts == _addresses(sender)
    assert emitter.frames_total == 1


def test_float_counts_each_landmark_address_individually():
    emitter, _ = _drive('float')
    counts = emitter.address_counts
    assert all(counts[f'/pose/lm/{i}'] == 1 for i in range(33))
    assert all(counts[f'/left_hand/lm/{i}'] == 1 for i in range(21))


def test_json_counts_one_address_per_payload():
    emitter, _ = _drive('json')
    assert emitter.address_counts['/pose/raw'] == 1
    assert not any(a.startswith('/pose/lm/') for a in emitter.address_counts)


def test_heartbeat_and_clear_are_counted_too():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'float')
    emitter.heartbeat()
    assert sum(emitter.address_counts.values()) == len(_addresses(sender) and sender.dgrams)
    before = sum(emitter.address_counts.values())
    emitter.pose_status(1)
    emitter.clear_all()
    assert sum(emitter.address_counts.values()) > before


def test_stats_snapshot_is_a_copy_with_sender_counters():
    emitter, sender = _drive('float')
    stats = emitter.stats()
    stats['per_address']['/x'] = 1
    assert '/x' not in emitter.address_counts
    assert stats['sent'] == len(sender.dgrams) and stats['dropped'] == 0 and stats['queue'] == 0


def test_detections_are_zero_the_moment_nothing_is_tracked():
    emitter = OscEmitter(CaptureSender(), 'float')
    assert emitter.take_detections() == {'pose': 0, 'hand': 0}
    emitter.pose_status(2)
    emitter.pose_status(0)   # a later idle frame in the same window does not hide the detection
    emitter.hand_status(1)
    assert emitter.take_detections() == {'pose': 2, 'hand': 1}
    # Next window: the person left. Not debounced, not cached.
    emitter.pose_status(0)
    emitter.hand_status(0)
    assert emitter.take_detections() == {'pose': 0, 'hand': 0}
    assert emitter.take_detections() == {'pose': 0, 'hand': 0}


# ============================================================================
# FRAME BUFFER
# ============================================================================
@pytest.fixture
def shm_name():
    name = make_shm_name()
    yield name
    unlink_shm_name(name)


def test_shm_names_fit_macos_limit():
    assert len(make_shm_name()) + 1 <= 30


def test_frame_round_trip_and_sequence(shm_name):
    writer = FrameWriter(shm_name, 8, 6)
    reader = FrameReader(shm_name)
    try:
        assert reader.info() == {'width': 8, 'height': 6, 'stride': 24}
        assert reader.read() is None  # nothing published yet
        first = np.full((6, 8, 3), 10, np.uint8)
        second = np.full((6, 8, 3), 200, np.uint8)
        assert writer.write(first) == 1
        seq, frame = reader.read()
        assert seq == 1 and (frame == first).all()
        assert reader.read() is None  # already seen
        assert reader.read(only_new=False)[0] == 1
        writer.write(second)
        writer.write(first)
        seq, frame = reader.read()
        assert seq == 3 and (frame == first).all()
    finally:
        reader.close()
        writer.close()


def test_wrong_size_frames_are_resized(shm_name):
    writer = FrameWriter(shm_name, 8, 6)
    reader = FrameReader(shm_name)
    try:
        writer.write(np.zeros((12, 16, 3), np.uint8))
        assert reader.read()[1].shape == (6, 8, 3)
    finally:
        reader.close()
        writer.close()


def test_torn_frame_is_rejected(shm_name):
    writer = FrameWriter(shm_name, 4, 4)
    reader = FrameReader(shm_name)
    try:
        writer.write(np.zeros((4, 4, 3), np.uint8))
        # Simulate the writer being mid-write on the published slot
        import struct
        struct.pack_into('<Q', writer._shm.buf, 32 + 8 * (1 & 1), (1 << 1) | 1)
        assert reader.read() is None
    finally:
        reader.close()
        writer.close()


def test_writer_close_unlinks_segment(shm_name):
    writer = FrameWriter(shm_name, 4, 4)
    writer.close()
    assert FrameReader(shm_name).info() is None


def test_reader_close_unlink_is_a_backstop(shm_name):
    writer = FrameWriter(shm_name, 4, 4)
    reader = FrameReader(shm_name)
    reader.info()
    reader.close(unlink=True)
    assert FrameReader(shm_name).info() is None
    writer.close()  # already gone: must not raise


# ============================================================================
# LIVE KEYS
# ============================================================================
def test_required_live_keys_are_declared():
    required = {
        'mediapipe.min_detection_confidence', 'mediapipe.min_tracking_confidence',
        'mediapipe.min_pose_presence_confidence', 'hand.min_detection_confidence',
        'hand.min_presence_confidence', 'hand.min_tracking_confidence',
        'osc.host', 'osc.port', 'osc.queue_size', 'osc.tracking_hold',
        'performance.target_fps', 'performance.show_fps',
        'display.mirror_preview', 'display.draw_landmarks', 'display.render_in_background',
        'display.landmark_color', 'display.connection_color', 'display.landmark_thickness',
        'display.landmark_radius', 'display.connection_thickness', 'display.connection_radius',
    }
    assert required <= LIVE_KEYS
    assert LANDMARKER_KEYS == {k for k in required if k.startswith(('mediapipe.min_', 'hand.min_'))}


@pytest.mark.parametrize('key', ['osc.protocol', 'camera.device_id', 'mediapipe.pose_model_type',
                                 'hand.num_hands', 'performance.force_cpu', 'ui.mode'])
def test_restart_class_keys_are_not_live(key):
    assert not is_live(key)


def test_live_keys_do_not_import_the_ml_stack():
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, '-c', 'import sys, src.live_keys, src.ipc_protocol, src.engine_link\n'
                               'assert "mediapipe" not in sys.modules and "cv2" not in sys.modules'],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('key,value,expected', [
    ('mediapipe.min_detection_confidence', 1, 1.0),
    ('osc.port', 9000.0, 9000),
    ('osc.host', ' 10.0.0.2 ', '10.0.0.2'),
    ('display.landmark_color', [1, 2, 3], [1, 2, 3]),
    ('performance.target_fps', 0, 0),
])
def test_coerce_accepts_and_normalizes(key, value, expected):
    assert coerce_live_value(key, value) == expected


@pytest.mark.parametrize('key,value', [
    ('mediapipe.min_detection_confidence', 1.5),
    ('mediapipe.min_detection_confidence', True),
    ('osc.port', 0),
    ('osc.port', 70000),
    ('osc.port', 'abc'),
    ('osc.host', ''),
    ('osc.queue_size', 1),
    ('performance.show_fps', 1),
    ('display.landmark_color', [1, 2]),
    ('display.landmark_color', [1, 2, 300]),
])
def test_coerce_rejects_bad_values(key, value):
    with pytest.raises(ValueError):
        coerce_live_value(key, value)


# ============================================================================
# ENGINE CHANNEL (real socketpair, fake launcher end)
# ============================================================================
class FakeEmitter:
    """Just the surface EngineChannel.tick reads"""

    def __init__(self):
        self.frames = 0
        self.tracking = {'pose': 0, 'hand': 0}

    def take_detections(self):
        return dict(self.tracking)

    def stats(self):
        return {'fps': 0, 'queue': 3, 'dropped': 1, 'sent': 42, 'frames': self.frames,
                'per_address': {'/pose/lm/0': self.frames}}


class Launcher:
    """The launcher's end of a socketpair, collecting what the engine says"""

    def __init__(self, sock):
        self.sock = sock
        self.reader = LineReader()
        self.messages = []
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                data = self.sock.recv(65536)
            except OSError:
                return
            if not data:
                return
            self.messages += self.reader.feed(data)

    def send(self, command):
        self.sock.sendall(encode_message(command))

    def statuses(self):
        return [m for m in self.messages if 'phase' in m]

    def replies(self):
        return [m for m in self.messages if 'ack' in m or 'error' in m]


@pytest.fixture
def pair():
    engine_sock, launcher_sock = socket.socketpair()
    launcher = Launcher(launcher_sock)
    closed = []
    channel = EngineChannel(engine_sock, on_close=lambda: closed.append(True), interval=0.02)
    channel.closed_calls = closed
    yield channel, launcher
    channel.close()
    launcher_sock.close()


def test_status_has_exactly_the_documented_fields(pair):
    channel, launcher = pair
    assert wait_for(lambda: launcher.statuses())
    status = launcher.statuses()[-1]
    assert set(status) == {'phase', 'fps', 'latency_ms', 'detections', 'queue', 'dropped', 'sent',
                           'per_address', 'uptime', 'frame_seq'}
    assert status['detections'] == {'pose': 0, 'hands': 0}


def test_status_streams_at_about_the_interval_and_reflects_counters(pair):
    channel, launcher = pair
    emitter = FakeEmitter()
    emitter.frames = 5
    emitter.tracking = {'pose': 1, 'hand': 2}
    channel.frame_done(12.0)
    channel.frame_done(8.0)
    channel.tick(emitter)
    assert wait_for(lambda: any(s['sent'] == 42 for s in launcher.statuses()))
    status = [s for s in launcher.statuses() if s['sent'] == 42][-1]
    assert status['queue'] == 3 and status['dropped'] == 1
    assert status['detections'] == {'pose': 1, 'hands': 2}
    assert status['latency_ms'] == 10.0
    assert status['per_address'] == {'/pose/lm/0': 5}
    n = len(launcher.statuses())
    time.sleep(0.3)
    assert len(launcher.statuses()) - n >= 5


def test_stale_snapshot_reports_zero_detections_and_rates():
    engine_sock, launcher_sock = socket.socketpair()
    now = [100.0]
    channel = EngineChannel(engine_sock, clock=lambda: now[0], interval=3600)
    try:
        emitter = FakeEmitter()
        emitter.frames, emitter.tracking = 10, {'pose': 2, 'hand': 2}
        channel.tick(emitter)
        now[0] += 0.5
        assert channel.status_message()['detections'] == {'pose': 2, 'hands': 2}
        now[0] += 2.0  # the loop stopped reporting (camera lost, model loading...)
        status = channel.status_message()
        assert status['detections'] == {'pose': 0, 'hands': 0}
        assert status['fps'] == 0.0 and status['latency_ms'] == 0.0
    finally:
        channel.close()
        launcher_sock.close()


def test_phase_changes_send_a_status_immediately_with_reason(pair):
    channel, launcher = pair
    channel.on_phase('reconnecting', 'Capture stopped delivering frames')
    assert wait_for(lambda: any(s['phase'] == 'reconnecting' for s in launcher.statuses()))
    status = [s for s in launcher.statuses() if s['phase'] == 'reconnecting'][-1]
    assert status['reason'] == 'Capture stopped delivering frames'
    channel.on_phase('ready')
    assert wait_for(lambda: launcher.statuses()[-1]['phase'] == 'ready')
    assert 'reason' not in launcher.statuses()[-1]


def test_set_live_key_is_queued_then_acked_by_the_loop(pair):
    channel, launcher = pair
    launcher.send({'cmd': 'set', 'key': 'osc.port', 'value': 9001})
    assert wait_for(lambda: channel._sets)
    assert channel.drain_sets() == [('osc.port', 9001, 1)]
    assert launcher.replies() == []  # the loop has not applied it yet
    channel.ack('osc.port')
    assert wait_for(lambda: {'ack': 'osc.port'} in launcher.replies())


def test_set_coalesces_repeats_but_keeps_one_ack_each(pair):
    channel, launcher = pair
    for value in (0.1, 0.2, 0.3):
        launcher.send({'cmd': 'set', 'key': 'mediapipe.min_detection_confidence', 'value': value})
    assert wait_for(lambda: channel._sets and channel._sets[0][2] == 3)
    assert channel.drain_sets() == [('mediapipe.min_detection_confidence', 0.3, 3)]


def test_set_rejects_restart_class_and_invalid_values(pair):
    channel, launcher = pair
    launcher.send({'cmd': 'set', 'key': 'camera.device_id', 'value': 1})
    launcher.send({'cmd': 'set', 'key': 'osc.port', 'value': 99999})
    assert wait_for(lambda: len(launcher.replies()) == 2)
    restart, invalid = launcher.replies()
    assert restart['key'] == 'camera.device_id' and 'restart' in restart['error']
    assert invalid['key'] == 'osc.port' and 'osc.port' in invalid['error']
    assert channel.drain_sets() == []


def test_unknown_command_is_an_error(pair):
    channel, launcher = pair
    launcher.send({'cmd': 'explode'})
    assert wait_for(lambda: launcher.replies())
    assert 'error' in launcher.replies()[0]


def test_stop_and_visible_commands(pair):
    channel, launcher = pair
    assert channel.visible and not channel.should_stop
    launcher.send({'cmd': 'visible', 'value': False})
    assert wait_for(lambda: not channel.visible)
    launcher.send({'cmd': 'stop'})
    assert wait_for(lambda: channel.should_stop)
    assert wait_for(lambda: {'ack': 'stop'} in launcher.replies())


def test_socket_close_marks_closed_and_fires_handler(pair):
    channel, launcher = pair
    launcher.sock.close()
    assert wait_for(channel.closed.is_set)
    assert channel.should_stop
    assert channel.closed_calls == [True]


def test_a_launcher_that_stops_reading_is_slow_not_gone():
    engine_sock, launcher_sock = socket.socketpair()
    closed = []
    channel = EngineChannel(engine_sock, on_close=lambda: closed.append(1), interval=0.001)
    try:
        # The launcher's socket is alive but nobody reads for longer than the send timeout
        emitter = FakeEmitter()
        emitter.stats = lambda: {'fps': 0, 'queue': 0, 'dropped': 0, 'sent': 0, 'frames': 1,
                                 'per_address': {f'/pose/lm/{n}': 1 for n in range(1000)}}
        channel.tick(emitter)
        launcher_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        time.sleep(1.8)
        assert not channel.closed.is_set() and closed == []
        # ...and once it reads again, status flows
        launcher = Launcher(launcher_sock)
        assert wait_for(lambda: launcher.statuses())
    finally:
        channel.close()
        launcher_sock.close()


def test_own_shutdown_is_not_mistaken_for_launcher_loss():
    engine_sock, launcher_sock = socket.socketpair()
    fired = []
    channel = EngineChannel(engine_sock, on_close=lambda: fired.append(1), interval=0.02)
    channel.close()
    time.sleep(0.1)
    assert fired == []
    launcher_sock.close()


def test_final_status_is_stopped_with_reason():
    engine_sock, launcher_sock = socket.socketpair()
    launcher = Launcher(launcher_sock)
    channel = EngineChannel(engine_sock, interval=0.02)
    channel.on_phase('ready')
    channel.close('capture_lost')
    assert wait_for(lambda: launcher.statuses() and launcher.statuses()[-1]['phase'] == 'stopped')
    assert launcher.statuses()[-1]['reason'] == 'capture_lost'
    launcher_sock.close()


def test_terminal_fault_survives_the_final_status():
    engine_sock, launcher_sock = socket.socketpair()
    launcher = Launcher(launcher_sock)
    channel = EngineChannel(engine_sock, interval=0.02)
    channel.on_phase('permission_denied', 'Camera access is off')
    channel.close('startup_failed')
    assert wait_for(lambda: launcher.statuses() and launcher.statuses()[-1]['phase'] == 'permission_denied')
    assert launcher.statuses()[-1]['reason'] == 'Camera access is off'
    launcher_sock.close()


def test_exit_on_launcher_loss_interrupts_and_arms_a_watchdog():
    calls = []
    hard = threading.Event()
    channel = SimpleNamespace(frames=None)
    handler = exit_on_launcher_loss(channel, grace=0.05, kill=lambda pid, sig: calls.append((pid, sig)),
                                    hard_exit=lambda code: (calls.append(('exit', code)), hard.set()), pid=77)
    handler()
    assert calls[0] == (77, signal.SIGTERM)
    assert hard.wait(2.0)
    assert calls[-1] == ('exit', 0)


def test_exit_within_a_second_of_socket_close_end_to_end():
    """Socket closes -> on_close fires -> hard exit scheduled well inside one second"""
    engine_sock, launcher_sock = socket.socketpair()
    exited = threading.Event()
    holder = {}
    channel = EngineChannel(engine_sock, on_close=lambda: holder['h'](), interval=0.02)
    holder['h'] = exit_on_launcher_loss(channel, grace=0.3, kill=lambda pid, sig: None,
                                        hard_exit=lambda code: exited.set())
    started = time.monotonic()
    launcher_sock.close()
    assert exited.wait(1.0)
    assert time.monotonic() - started < 1.0
    channel.close()


def test_channel_publishes_frames_with_mirror(shm_name):
    engine_sock, launcher_sock = socket.socketpair()
    channel = EngineChannel(engine_sock, FrameWriter(shm_name, 4, 2), interval=0.02)
    reader = FrameReader(shm_name)
    try:
        frame = np.zeros((2, 4, 3), np.uint8)
        frame[:, 0] = 255
        channel.publish_frame(frame)
        seq, plain = reader.read()
        assert seq == 1 and plain[0, 0, 0] == 255 and plain[0, 3, 0] == 0
        channel.publish_frame(frame, mirror=True)
        seq, mirrored = reader.read()
        assert seq == 2 and mirrored[0, 3, 0] == 255 and mirrored[0, 0, 0] == 0
        assert channel.status_message()['frame_seq'] == 2
    finally:
        reader.close()
        channel.close()
        launcher_sock.close()


def test_null_channel_is_inert():
    channel = NullChannel()
    channel.on_phase('ready')
    channel.tick(None)
    channel.publish_frame(None)
    channel.close()
    assert channel.drain_sets() == [] and not channel.should_stop and not channel.frames_wanted


# ============================================================================
# ENGINE LINK (launcher side) against a fake engine
# ============================================================================
def _fake_engine_socket(link):
    return socket.socket(fileno=os.dup(link.child_fd))


def test_link_argv_and_pass_fds():
    link = EngineLink()
    try:
        assert link.argv[:2] == ['--ipc-fd', str(link.child_fd)]
        assert link.argv[2:] == ['--ipc-shm', link.shm_name]
        assert link.pass_fds == (link.child_fd,)
        assert os.get_inheritable(link.child_fd)
    finally:
        link.close()


def test_link_without_frames_omits_shm():
    link = EngineLink(frames=False)
    try:
        assert '--ipc-shm' not in link.argv
        assert link.read_frame() is None and link.frame_info() is None
    finally:
        link.close()


def test_link_parses_status_replies_and_sends_commands():
    statuses, replies, closed = [], [], threading.Event()
    link = EngineLink(on_status=statuses.append, on_reply=replies.append, on_closed=closed.set)
    engine = _fake_engine_socket(link)
    link.spawned()
    try:
        # Lines split across writes, status and replies interleaved
        data = (encode_message({'phase': 'loading_model', 'frame_seq': 0})
                + encode_message({'ack': 'osc.port'})
                + encode_message({'error': 'nope', 'key': 'osc.host'}))
        engine.sendall(data[:10])
        engine.sendall(data[10:])
        assert wait_for(lambda: statuses and len(replies) == 2)
        assert statuses[0]['phase'] == 'loading_model' and link.last_status == statuses[0]
        assert replies == [{'ack': 'osc.port'}, {'error': 'nope', 'key': 'osc.host'}]

        assert link.send({'cmd': 'stop'})
        assert LineReader().feed(engine.recv(4096)) == [{'cmd': 'stop'}]

        engine.close()
        assert closed.wait(2.0)
        assert not link.send({'cmd': 'stop'})
    finally:
        link.close()


def test_link_reads_frames_the_engine_writes():
    link = EngineLink()
    try:
        assert link.read_frame() is None  # segment not created yet
        writer = FrameWriter(link.shm_name, 6, 4)
        try:
            frame = np.arange(6 * 4 * 3, dtype=np.uint8).reshape(4, 6, 3)
            writer.write(frame)
            assert link.frame_info() == {'width': 6, 'height': 4, 'stride': 18}
            seq, got = link.read_frame()
            assert seq == 1 and (got == frame).all()
            assert link.read_frame() is None
        finally:
            writer.close()
    finally:
        link.close()


def test_link_close_unlinks_the_segment_of_a_dead_engine():
    link = EngineLink()
    writer = FrameWriter(link.shm_name, 4, 4)  # an engine that never cleans up
    writer.write(np.zeros((4, 4, 3), np.uint8))
    assert link.read_frame() is not None
    name = link.shm_name
    link.close()
    assert FrameReader(name).info() is None
    writer.close()


def test_link_and_real_engine_channel_talk_both_ways():
    statuses, replies = [], []
    link = EngineLink(on_status=statuses.append, on_reply=replies.append)
    channel = EngineChannel(socket.socket(fileno=os.dup(link.child_fd)),
                            FrameWriter(link.shm_name, 4, 4), interval=0.02)
    link.spawned()
    try:
        channel.on_phase('ready')
        assert wait_for(lambda: statuses and statuses[-1]['phase'] == 'ready')
        link.send({'cmd': 'set', 'key': 'osc.tracking_hold', 'value': 0.5})
        assert wait_for(lambda: channel._sets)
        (key, value, count), = channel.drain_sets()
        channel.ack(key, count)
        assert wait_for(lambda: replies == [{'ack': 'osc.tracking_hold'}])
        channel.publish_frame(np.full((4, 4, 3), 7, np.uint8))
        assert wait_for(lambda: statuses[-1]['frame_seq'] == 1)
        assert link.read_frame()[0] == 1
        # Launcher goes away -> the engine's end sees it
        link.close()
        assert wait_for(channel.closed.is_set)
    finally:
        channel.close()
        link.close()


# ============================================================================
# LIVE SETTINGS
# ============================================================================
class FakeProcessor:
    label = 'Pose'

    def __init__(self, fail_rebuild=False):
        self.draw_enabled = True
        self.show_fps = False
        self.specs_refreshed = 0
        self.rebuilds = 0
        self.fail_rebuild = fail_rebuild

    def refresh_display_specs(self):
        self.specs_refreshed += 1

    def set_show_fps(self, enabled):
        self.show_fps = enabled

    def rebuild_landmarker(self, old):
        if self.fail_rebuild:
            raise RuntimeError("boom")
        self.rebuilds += 1
        return f"{old}+"


class FakeSender:
    def __init__(self):
        self.client = 'old'
        self.queue_size = 32

    def set_queue_size(self, size):
        self.queue_size = size


class FakeChannel(NullChannel):
    def __init__(self, sets=(), frames=True):
        super().__init__()
        self._sets = list(sets)
        self._frames = frames
        self.acks, self.errors = [], []

    frames_wanted = property(lambda self: self._frames)

    def drain_sets(self):
        sets, self._sets = self._sets, []
        return sets

    def ack(self, key, count=1):
        self.acks += [key] * count

    def error(self, key, message, count=1):
        self.errors += [(key, message)] * count


def make_live(sets=(), pose=None, hand=None, **kwargs):
    from src.config import Config
    import tempfile
    config = Config(os.path.join(tempfile.mkdtemp(), 'config.json'))
    channel = kwargs.pop('channel', None) or FakeChannel(sets)
    emitter = OscEmitter(CaptureSender(), 'float', config)
    sender = FakeSender()
    clock = runtime.FrameClock(1 / 30)
    live = LiveSettings(config, emitter, sender, clock, pose, hand, 'pose_lm', 'hand_lm', channel, **kwargs)
    return live, config, channel, emitter, sender, clock


def test_live_threshold_rebuilds_once_for_a_batch_and_acks_each():
    pose = FakeProcessor()
    live, config, channel, *_ = make_live(
        [('mediapipe.min_detection_confidence', 0.4, 1), ('mediapipe.min_tracking_confidence', 0.3, 2)],
        pose=pose)
    live.apply_pending()
    assert config.get('mediapipe', 'min_detection_confidence') == 0.4
    assert pose.rebuilds == 1 and live.pose_landmarker == 'pose_lm+'
    assert channel.acks == ['mediapipe.min_detection_confidence'] + ['mediapipe.min_tracking_confidence'] * 2
    assert channel.errors == []


def test_live_unchanged_threshold_does_not_rebuild():
    pose = FakeProcessor()
    live, config, channel, *_ = make_live([('mediapipe.min_detection_confidence', 0.7, 1)], pose=pose)
    live.apply_pending()
    assert pose.rebuilds == 0 and channel.acks == ['mediapipe.min_detection_confidence']


def test_live_failed_rebuild_rolls_back_and_errors():
    pose = FakeProcessor(fail_rebuild=True)
    live, config, channel, *_ = make_live([('mediapipe.min_detection_confidence', 0.4, 1)], pose=pose)
    live.apply_pending()
    assert config.get('mediapipe', 'min_detection_confidence') == 0.7
    assert live.pose_landmarker == 'pose_lm'
    assert channel.acks == [] and channel.errors[0][0] == 'mediapipe.min_detection_confidence'


def test_live_hand_thresholds_only_rebuild_the_hand_landmarker():
    pose, hand = FakeProcessor(), FakeProcessor()
    live, *_ = make_live([('hand.min_tracking_confidence', 0.2, 1)], pose=pose, hand=hand)
    live.apply_pending()
    assert (pose.rebuilds, hand.rebuilds) == (0, 1)


def test_live_holistic_ignores_hand_thresholds_it_has_no_option_for():
    holistic = FakeProcessor()
    holistic.label = 'Holistic'
    live, config, channel, *_ = make_live(
        [('hand.min_tracking_confidence', 0.2, 1), ('hand.min_presence_confidence', 0.2, 1)], pose=holistic)
    live.apply_pending()
    assert holistic.rebuilds == 1  # only the presence threshold reaches the model
    assert config.get('hand', 'min_tracking_confidence') == 0.2  # still stored
    assert channel.errors == []


def test_live_osc_target_and_queue_and_hold_and_fps():
    live, config, channel, emitter, sender, clock = make_live([
        ('osc.host', '127.0.0.1', 1), ('osc.port', 9100, 1), ('osc.queue_size', 64, 1),
        ('osc.tracking_hold', 2.5, 1), ('performance.target_fps', 60, 1)])
    live.apply_pending()
    assert sender.client != 'old' and sender.client._address == '127.0.0.1' and sender.client._port == 9100
    assert sender.queue_size == 64
    assert all(t.hold == 2.5 for t in emitter._trackers.values())
    assert clock.interval == pytest.approx(1 / 60)
    assert config.get('osc', 'port') == 9100
    assert channel.errors == []

    live.channel._sets = [('performance.target_fps', 0, 1)]
    live.apply_pending()
    assert clock.interval == 0


def test_live_unresolvable_host_is_an_error_and_keeps_the_old_target():
    live, config, channel, _, sender, _ = make_live([('osc.host', 'no such host.invalid', 1)])
    live.apply_pending()
    assert sender.client == 'old'
    assert config.get('osc', 'host') == '127.0.0.1'
    assert channel.errors and channel.errors[0][0] == 'osc.host'


def test_live_show_fps_respects_the_hand_processors_rule():
    pose, hand = FakeProcessor(), FakeProcessor()
    live, *_ = make_live([('performance.show_fps', True, 1)], pose=pose, hand=hand)
    live.apply_pending()
    assert pose.show_fps is True and hand.show_fps is False


def test_live_display_keys_refresh_the_drawing_specs():
    pose = FakeProcessor()
    live, config, *_ = make_live([('display.landmark_color', [1, 2, 3], 1)], pose=pose)
    live.apply_pending()
    assert pose.specs_refreshed == 1 and config.get('display', 'landmark_color') == [1, 2, 3]


def test_live_mirror_and_draw_flags():
    pose = FakeProcessor()
    live, *_ = make_live([('display.mirror_preview', True, 1), ('display.draw_landmarks', False, 1)], pose=pose)
    live.apply_pending()
    assert live.mirror is True and pose.draw_enabled is False


def test_hidden_preview_skips_drawing_and_frames_unless_background_rendering():
    pose = FakeProcessor()
    channel = FakeChannel()
    live, *_ = make_live(pose=pose, channel=channel)
    assert live.publish_frames and pose.draw_enabled
    channel.visible = False
    live.update_draw_state()
    assert not live.publish_frames and not pose.draw_enabled
    live.channel._sets = [('display.render_in_background', True, 1)]
    live.apply_pending()
    assert live.publish_frames and pose.draw_enabled


def test_preview_window_always_draws():
    pose = FakeProcessor()
    channel = FakeChannel(frames=False)
    channel.visible = False
    live, *_ = make_live(pose=pose, channel=channel, preview_window=True)
    assert pose.draw_enabled and not live.publish_frames


# ============================================================================
# PHASES
# ============================================================================
def test_fault_and_stopped_phases_are_reportable(capsys):
    seen = []
    runtime.set_phase_handler(lambda phase, reason: seen.append((phase, reason)))
    try:
        for phase in runtime.ALL_PHASES:
            runtime.report_phase(phase, reason='why')
        with pytest.raises(ValueError):
            runtime.report_phase('exploded')
    finally:
        runtime.set_phase_handler(None)
    assert [p for p, _ in seen] == list(runtime.ALL_PHASES)
    assert runtime.PHASES == ('opening_camera', 'loading_model', 'warming_up', 'ready')


def test_reconnecting_capture_reports_state_changes():
    from tests.test_runtime import FakeCap, make_capture
    states = []
    cap = FakeCap([True, False, False, True])
    wrapper, clock, sleep = make_capture(cap, timeout=5)
    wrapper._on_state = lambda state, reason: states.append(state)
    for _ in range(4):
        wrapper.read()
        clock.now += 1
    assert states == ['reconnecting', 'recovered']


def test_reconnecting_capture_reports_giving_up():
    from tests.test_runtime import FakeCap, make_capture
    states = []
    wrapper, clock, sleep = make_capture(FakeCap([], opened=True), timeout=3)
    wrapper._on_state = lambda state, reason: states.append((state, reason))
    for _ in range(10):
        wrapper.read()
        clock.now += 1
        if wrapper.gave_up:
            break
    assert [s for s, _ in states] == ['reconnecting', 'gave_up']
    assert 'lost' in states[-1][1].lower()


def test_capture_states_map_onto_phases(monkeypatch):
    import main
    seen = []
    monkeypatch.setattr(main, 'report_phase', lambda phase, reason="", message=None: seen.append(phase))
    monkeypatch.setattr(main, 'camera_permission_denied', lambda: False)
    config = None
    main.report_capture_state(config, False, 'reconnecting', 'r')
    main.report_capture_state(config, False, 'recovered', '')
    main.report_capture_state(config, False, 'gave_up', 'r')
    monkeypatch.setattr(main, 'camera_permission_denied', lambda: True)
    main.report_capture_state(config, False, 'gave_up', 'r')
    main.report_capture_state(config, True, 'gave_up', 'r')  # NDI: never a permission problem
    assert seen == ['reconnecting', 'ready', 'no_camera', 'permission_denied', 'no_camera']


def test_unopenable_webcam_reports_permission_or_no_camera(monkeypatch):
    import main
    from src.config import Config
    seen = []
    monkeypatch.setattr(main, 'report_phase', lambda phase, reason="", message=None: seen.append((phase, reason)))
    config = Config(os.path.join(os.path.dirname(__file__), 'nonexistent.json'))
    monkeypatch.setattr(main, 'camera_permission_denied', lambda: True)
    main.report_camera_unavailable(config)
    monkeypatch.setattr(main, 'camera_permission_denied', lambda: False)
    main.report_camera_unavailable(config)
    assert [p for p, _ in seen] == ['permission_denied', 'no_camera']
    assert 'Privacy' in seen[0][1]


def test_camera_authorization_is_unknown_off_macos(monkeypatch):
    monkeypatch.setattr(camera_permission.platform, 'system', lambda: 'Linux')
    assert camera_permission.camera_authorization() == 'unknown'
    assert camera_permission.camera_permission_denied() is False


@pytest.mark.parametrize('status,denied', [('denied', True), ('restricted', True),
                                           ('authorized', False), ('not_determined', False),
                                           ('unknown', False)])
def test_permission_denied_statuses(monkeypatch, status, denied):
    monkeypatch.setattr(camera_permission, 'camera_authorization', lambda: status)
    assert camera_permission.camera_permission_denied() is denied
