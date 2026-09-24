"""
OscEmitter + formatters (src/osc_protocol.py):

- legacy reproduces the frozen 0.2.x wire output byte for byte
  (tests/fixtures/osc_golden.json, captured before the refactor)
- JSON v2 shape (no per-landmark type/id, top-level person, no null
  visibility) and its size reduction over legacy
- bundled protocols stay inside the MTU budget and keep message order
- /…/tracking hysteresis, the 1 Hz heartbeat, and the shutdown clear
"""
import json
import threading

import pytest
from pythonosc.osc_bundle import OscBundle
from pythonosc.osc_message import OscMessage
from pythonosc.osc_packet import OscPacket

from src.osc_protocol import (
    FORMATTERS,
    MAX_BUNDLE_BYTES,
    JsonFormatter,
    LegacyFormatter,
    OscEmitter,
    OscFormatter,
    TrackingHysteresis,
    build_message,
    make_formatter,
    pack_bundles,
)
from src.osc_sender import ThreadedOSCSender
from src.pose_utils import LetterboxTransform
from tests import golden_harness as golden
from tests.golden_harness import CaptureSender, fake_landmarks


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def _messages(dgram):
    """Every OscMessage in a datagram (a bare message or a bundle), in order"""
    if OscBundle.dgram_is_bundle(dgram):
        return [timed.message for timed in OscPacket(dgram).messages]
    return [OscMessage(dgram)]


def _all_messages(sender):
    return [m for d in sender.dgrams for m in _messages(d)]


def _payload(msg):
    return json.loads(msg.params[0])


# ============================================================================
# LEGACY: GOLDEN PARITY
# ============================================================================
GOLDEN = golden.load_fixture()


@pytest.fixture(scope='module')
def legacy_capture():
    return golden.capture()


@pytest.mark.parametrize('scenario', sorted(GOLDEN))
def test_legacy_output_is_byte_identical_to_0_2_x(scenario, legacy_capture):
    expected = GOLDEN[scenario]
    actual = legacy_capture[scenario]
    assert len(actual) == len(expected)
    for frame, (got, want) in enumerate(zip(actual, expected)):
        assert [_messages(d)[0].address for d in got] == [_messages(d)[0].address for d in want], f"frame {frame}"
        assert got == want, f"{scenario} frame {frame}: bytes differ"


def test_golden_scenarios_run_without_processor_errors(capsys):
    # process_frame swallows exceptions - make sure none were hit, or a
    # broken path could "match" by sending nothing on both sides
    golden.capture()
    assert 'error' not in capsys.readouterr().out.lower()


def test_legacy_never_bundles():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'legacy')
    emitter.begin_frame(1.0)
    emitter.pose(0, fake_landmarks(33, 0), fake_landmarks(33, 1, world=True), None, 'pose_0')
    emitter.pose_status(1)
    emitter.end_frame()
    emitter.heartbeat()
    emitter.clear_all()
    assert sender.dgrams
    assert not any(OscBundle.dgram_is_bundle(d) for d in sender.dgrams)


def test_legacy_additive_channels_use_native_args():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'legacy', clock=FakeClock())
    emitter.begin_frame(1.0)
    emitter.pose_status(2)
    emitter.hand_status(1)
    emitter.end_frame()
    emitter.heartbeat(fps=29.5)
    msgs = _all_messages(sender)
    by_address = {m.address: m for m in msgs}
    assert by_address['/mp/status'].params == ['{"status":2}']
    assert by_address['/hand/status'].params == ['{"status":1}']
    assert by_address['/mp/tracking'].params == [2]
    assert by_address['/hand/tracking'].params == [1]
    heartbeat = by_address['/mp/heartbeat']
    assert heartbeat.params == [29.5, 0, 0, 2 + 2]  # CaptureSender counts sent datagrams
    assert b',fiii' in heartbeat.dgram


# ============================================================================
# JSON V2
# ============================================================================
def test_json_pose_payload_shape():
    lms = fake_landmarks(33, 0)
    out = dict(JsonFormatter().pose(5.0, 1, lms, fake_landmarks(33, 1, world=True), None, 'pose_1'))
    raw = json.loads(out['/pose/raw'])
    assert raw['timestamp'] == 5.0
    assert raw['person'] == 1
    assert len(raw['landmarks']) == 33
    assert set(raw['landmarks'][0]) == {'x', 'y', 'z', 'visibility'}
    assert raw['landmarks'][3]['x'] == round(lms[3].x, 3)
    assert json.loads(out['/pose/world'])['person'] == 1
    # Bounds keep "id" - there it names the extreme landmark
    bounds = json.loads(out['/pose/raw_bounds'])
    assert bounds['person'] == 1
    assert set(bounds['max_x']) >= {'id', 'x', 'y', 'z'}


def test_json_omits_null_visibility():
    class Bare:
        def __init__(self, x):
            self.x, self.y, self.z = x, x, x

    class NoneVis(Bare):
        visibility = None

    out = dict(JsonFormatter().hand(0.0, 'Left', [Bare(0.1), NoneVis(0.2)], None, None, 'hand_0'))
    landmarks = json.loads(out['/left_hand/raw'])['landmarks']
    assert landmarks == [{'x': 0.1, 'y': 0.1, 'z': 0.1}, {'x': 0.2, 'y': 0.2, 'z': 0.2}]
    assert '/left_hand/world' not in out


def test_json_applies_letterbox_transform_to_normalized_only():
    transform = LetterboxTransform(0.5, 0, 60, 640, 480, 1280, 720)
    lms, world = fake_landmarks(33, 0), fake_landmarks(33, 1, world=True)
    legacy = dict(LegacyFormatter().pose(0.0, 0, lms, world, transform, 'pose_0'))
    new = dict(JsonFormatter().pose(0.0, 0, lms, world, transform, 'pose_0'))
    for address in ('/pose/raw', '/pose/world'):
        old_pts = [(d['x'], d['y'], d['z']) for d in json.loads(legacy[address])['landmarks']]
        new_pts = [(d['x'], d['y'], d['z']) for d in json.loads(new[address])['landmarks']]
        assert old_pts == new_pts


def test_json_world_landmarks_and_world_bounds_carry_no_visibility():
    fmt = JsonFormatter()
    pose = dict(fmt.pose(0.0, 0, fake_landmarks(33, 0), fake_landmarks(33, 1, world=True), None, 'pose_0'))
    hand = dict(fmt.hand(0.0, 'Left', fake_landmarks(21, 2), fake_landmarks(21, 3, world=True), None, 'hand_0'))
    for payload in (pose['/pose/world'], hand['/left_hand/world']):
        entries = json.loads(payload)['landmarks']
        assert entries and all(set(d) == {'x', 'y', 'z'} for d in entries)
    for payload in (pose['/pose/world_bounds'], hand['/left_hand/world_bounds']):
        extremes = {k: v for k, v in json.loads(payload).items() if k != 'person'}
        assert extremes and all('visibility' not in v for v in extremes.values())
    # Normalized landmarks and their bounds keep visibility
    assert all('visibility' in d for d in json.loads(pose['/pose/raw'])['landmarks'])
    assert all('visibility' in d for d in json.loads(hand['/left_hand/raw'])['landmarks'])
    assert 'visibility' in json.loads(pose['/pose/raw_bounds'])['max_x']


def test_json_pose_world_fits_one_bundle():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'json')
    emitter.pose(0, fake_landmarks(33, 0), fake_landmarks(33, 1, world=True), None, 'pose_0')
    world = [d for d in sender.dgrams if any(m.address == '/pose/world' for m in _messages(d))]
    assert len(world) == 1 and len(world[0]) <= MAX_BUNDLE_BYTES


def test_json_multi_person_tags_each_pose():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'json')
    emitter.begin_frame(1.0)
    for person in range(3):
        emitter.pose(person, fake_landmarks(33, person), None, None, f'pose_{person}')
    emitter.pose_status(3)
    emitter.end_frame()
    raws = [_payload(m) for m in _all_messages(sender) if m.address == '/pose/raw']
    assert [r['person'] for r in raws] == [0, 1, 2]


def test_json_clears_carry_no_status_but_hands_keep_handedness():
    pose = JsonFormatter().pose_cleared(2.0)
    assert [a for a, _ in pose] == ['/pose/raw', '/pose/raw_bounds', '/pose/world', '/pose/world_bounds']
    both = JsonFormatter().hand_cleared(2.0, None)
    assert [a for a, _ in both][::4] == ['/left_hand/raw', '/right_hand/raw']
    assert json.loads(both[0][1])['handedness'] == 'Left'


@pytest.mark.parametrize('kind,address', [('pose', '/pose/raw'), ('hand', '/left_hand/raw')])
def test_json_is_much_smaller_than_legacy(kind, address):
    lms = fake_landmarks(33 if kind == 'pose' else 21, 0)
    if kind == 'pose':
        legacy = dict(LegacyFormatter().pose(1700000000.123456, 0, lms, None, None, 'pose_0'))
        new = dict(JsonFormatter().pose(1700000000.123456, 0, lms, None, None, 'pose_0'))
    else:
        legacy = dict(LegacyFormatter().hand(1700000000.123456, 'Left', lms, None, None, 'hand_0'))
        new = dict(JsonFormatter().hand(1700000000.123456, 'Left', lms, None, None, 'hand_0'))
    legacy_size = build_message(address, legacy[address]).size
    new_size = build_message(address, new[address]).size
    assert new_size < legacy_size * 0.75, (legacy_size, new_size)


def test_json_status_and_tracking_are_native_ints_under_gesture():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'json', clock=FakeClock())
    emitter.begin_frame(1.0)
    emitter.pose_status(1)
    emitter.hand_status(0)
    emitter.end_frame()
    emitter.heartbeat(fps=30.0)
    assert len(sender.dgrams) == 2  # the frame's bundle, then the heartbeat's
    assert all(OscBundle.dgram_is_bundle(d) for d in sender.dgrams)
    msgs = {m.address: m.params for m in _all_messages(sender)}
    assert msgs['/gesture/pose/status'] == [1]
    assert msgs['/gesture/hand/status'] == [0]
    assert msgs['/gesture/pose/tracking'] == [1]
    assert msgs['/gesture/hand/tracking'] == [0]
    assert msgs['/gesture/heartbeat'][0] == 30.0
    assert not any(a.startswith('/mp/') for a in msgs)


# ============================================================================
# BUNDLES / MTU BUDGET
# ============================================================================
def _msg(address, size):
    """A single-string-arg message whose datagram is exactly `size` bytes"""
    for n in range(size):
        msg = build_message(address, 'x' * n)
        if msg.size == size:
            return msg
    raise AssertionError(f"no {size}-byte message for {address}")


def test_pack_bundles_splits_at_the_budget_and_keeps_order():
    msgs = [_msg(f'/m{i}', 400) for i in range(7)]
    bundles = pack_bundles(msgs, 1400)
    # 16 header + 3 * (4 + 400) = 1228; a 4th would be 1632
    assert [len(b.dgram) for b in bundles] == [1228, 1228, 420]
    flat = [m.address for b in bundles for m in _messages(b.dgram)]
    assert flat == [m.address for m in msgs]


def test_pack_bundles_sends_an_oversized_message_alone():
    msgs = [_msg('/small', 100), _msg('/huge', 1600), _msg('/small2', 100)]
    bundles = pack_bundles(msgs, 1400)
    assert [[m.address for m in _messages(b.dgram)] for b in bundles] == [['/small'], ['/huge'], ['/small2']]


def test_bundled_frame_stays_within_budget():
    """A full holistic-style frame in json mode: every bundle that holds more
    than one message fits the budget, and nothing is lost or reordered"""
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'json')
    transform = LetterboxTransform(0.5, 0, 60, 640, 480, 1280, 720)
    emitter.begin_frame(1.0)
    emitter.pose(0, fake_landmarks(33, 0), fake_landmarks(33, 1, world=True), transform, 'pose_0')
    emitter.pose_status(1)
    emitter.hand('Left', fake_landmarks(21, 2), fake_landmarks(21, 3, world=True), transform, 'hand_left')
    emitter.hand('Right', fake_landmarks(21, 4), fake_landmarks(21, 5, world=True), transform, 'hand_right')
    emitter.hand_status(2)
    emitter.end_frame()

    assert len(sender.dgrams) > 1
    for d in sender.dgrams:
        assert OscBundle.dgram_is_bundle(d)
        if len(_messages(d)) > 1:
            assert len(d) <= MAX_BUNDLE_BYTES
    addresses = [m.address for m in _all_messages(sender)]
    assert addresses == [
        '/pose/raw', '/pose/world', '/pose/raw_bounds', '/pose/world_bounds', '/gesture/pose/status',
        '/left_hand/raw', '/left_hand/world', '/left_hand/bounds', '/left_hand/world_bounds',
        '/right_hand/raw', '/right_hand/world', '/right_hand/bounds', '/right_hand/world_bounds',
        '/gesture/hand/status', '/gesture/pose/tracking', '/gesture/hand/tracking',
    ]


def test_hand_frame_bundles_fit_the_mtu():
    # Hand JSON v2 messages are each under the budget, so every bundle fits
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'json')
    emitter.begin_frame(1.0)
    emitter.hand('Left', fake_landmarks(21, 2), fake_landmarks(21, 3, world=True), None, 'hand_0')
    emitter.hand('Right', fake_landmarks(21, 4), fake_landmarks(21, 5, world=True), None, 'hand_1')
    emitter.hand_status(2)
    emitter.end_frame()
    assert all(len(d) <= MAX_BUNDLE_BYTES for d in sender.dgrams)


def test_begin_frame_flushes_an_unfinished_frame():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'json')
    emitter.begin_frame(1.0)
    emitter.pose_status(0)  # ...and the processor raised before end_frame
    emitter.begin_frame(2.0)
    assert [m.address for m in _all_messages(sender)] == ['/gesture/pose/status', '/gesture/pose/tracking']


def test_outside_a_frame_bundled_protocols_send_immediately():
    sender = CaptureSender()
    OscEmitter(sender, 'json').pose_status(0)
    assert len(sender.dgrams) == 1 and OscBundle.dgram_is_bundle(sender.dgrams[0])


# ============================================================================
# TRACKING HYSTERESIS
# ============================================================================
def test_tracking_rises_immediately_and_holds_through_dropouts():
    clock = FakeClock()
    tracker = TrackingHysteresis(hold=0.3, clock=clock)
    assert tracker.value == 0
    tracker.update(1)
    assert tracker.value == 1
    for _ in range(5):  # detector skipped frames - raw status says 0
        clock.t += 0.05
        tracker.update(0)
        assert tracker.value == 1
    clock.t += 0.1  # now 0.35s since the last nonzero report
    tracker.update(0)
    assert tracker.value == 0


def test_tracking_reports_the_highest_recent_count():
    clock = FakeClock()
    tracker = TrackingHysteresis(hold=0.3, clock=clock)
    tracker.update(2)
    clock.t += 0.1
    tracker.update(1)  # second person flickered out for one frame
    assert tracker.value == 2
    clock.t += 0.25
    tracker.update(1)
    assert tracker.value == 1


def test_status_is_never_debounced():
    # Status channels send 0 whenever nothing is tracked this frame - only
    # tracking holds a count
    clock = FakeClock()
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'legacy', clock=clock)
    for n in (1, 0, 0):
        emitter.begin_frame(1.0)
        emitter.pose_status(n)
        emitter.end_frame()
        clock.t += 0.05
    msgs = [(m.address, m.params[0]) for m in _all_messages(sender)]
    assert msgs == [
        ('/mp/status', '{"status":1}'), ('/mp/tracking', 1),
        ('/mp/status', '{"status":0}'), ('/mp/tracking', 1),
        ('/mp/status', '{"status":0}'), ('/mp/tracking', 1),
    ]


def test_tracking_only_for_channels_that_reported_this_frame():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'legacy')
    emitter.begin_frame(1.0)
    emitter.pose_status(0)
    emitter.end_frame()
    emitter.begin_frame(2.0)  # skipped frame: no processor reported anything
    emitter.end_frame()
    assert [m.address for m in _all_messages(sender)] == ['/mp/status', '/mp/tracking']


def test_tracking_hold_comes_from_config():
    class Cfg:
        def get(self, section, key=None, default=None):
            return 1.5 if (section, key) == ('osc', 'tracking_hold') else default

    emitter = OscEmitter(CaptureSender(), 'json', config=Cfg())
    assert emitter._trackers['pose'].hold == 1.5


# ============================================================================
# HEARTBEAT
# ============================================================================
def test_heartbeat_is_rate_limited_to_1hz_and_reports_loop_fps():
    clock = FakeClock()
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'legacy', clock=clock)
    assert emitter.heartbeat() is True  # first call sends right away
    for i in range(30):
        clock.t = 100.0 + i / 30  # < 1s after the first beat
        assert emitter.heartbeat() is False
        emitter.begin_frame()
        emitter.end_frame()
    clock.t = 101.0
    assert emitter.heartbeat() is True
    beats = [m for m in _all_messages(sender) if m.address == '/mp/heartbeat']
    assert len(beats) == 2
    assert beats[0].params[0] == 0.0
    assert beats[1].params[0] == pytest.approx(30.0, rel=1e-5)


def test_heartbeat_counters_wrap_to_int32():
    args = OscFormatter.heartbeat(OscFormatter(), 1.0, 0, 0, 2 ** 31 + 5)[0][1]
    assert args[3] == 5
    build_message('/x', args)  # must stay encodable as 'i'


# ============================================================================
# SHUTDOWN CLEAR
# ============================================================================
def test_clear_all_legacy_clears_used_channels():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'legacy')
    emitter.pose_status(1)
    emitter.hand_status(1)
    sender.dgrams.clear()
    emitter.clear_all(ts=3.0)
    msgs = _all_messages(sender)
    addresses = [m.address for m in msgs]
    assert addresses == [
        '/pose/raw', '/pose/raw_bounds', '/pose/world', '/pose/world_bounds', '/mp/status',
        '/mp/status', '/mp/tracking',
        '/left_hand/raw', '/left_hand/world', '/left_hand/bounds', '/left_hand/world_bounds',
        '/right_hand/raw', '/right_hand/world', '/right_hand/bounds', '/right_hand/world_bounds',
        '/hand/status', '/hand/tracking',
    ]
    assert _payload(msgs[0]) == {'timestamp': 3.0, 'landmarks': []}
    assert msgs[6].params == [0]


def test_clear_all_sends_nothing_for_unused_channels():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'json')
    emitter.pose_status(1)
    sender.dgrams.clear()
    emitter.clear_all()
    addresses = [m.address for m in _all_messages(sender)]
    assert '/pose/raw' in addresses and '/gesture/pose/tracking' in addresses
    assert not any('hand' in a for a in addresses)


def test_clear_all_resets_tracking():
    emitter = OscEmitter(CaptureSender(), 'json')
    emitter.pose_status(2)
    emitter.clear_all()
    assert emitter._trackers['pose'].value == 0


def test_clear_all_reaches_the_wire_through_a_flushing_stop():
    class SlowClient:
        def __init__(self):
            self.sent = []
            self.gate = threading.Event()

        def send(self, packet):
            self.gate.wait(timeout=2.0)
            self.sent.append(packet.dgram)

    client = SlowClient()
    sender = ThreadedOSCSender(client, queue_size=64)
    emitter = OscEmitter(sender, 'legacy')
    emitter.pose(0, fake_landmarks(33, 0), None, None, 'pose_0')
    emitter.pose_status(1)
    emitter.clear_all()
    client.gate.set()
    sender.stop(flush=True)
    addresses = [OscMessage(d).address for d in client.sent]
    assert addresses[-2:] == ['/mp/status', '/mp/tracking']
    assert addresses.count('/pose/raw') == 2  # the pose, then its clear


# ============================================================================
# FORMATTER REGISTRY
# ============================================================================
def test_registry_has_legacy_and_json():
    assert FORMATTERS['legacy'] is LegacyFormatter
    assert FORMATTERS['json'] is JsonFormatter
    assert not LegacyFormatter.bundled and JsonFormatter.bundled


def test_unknown_protocol_falls_back_to_legacy(capsys):
    assert isinstance(make_formatter('no-such-protocol'), LegacyFormatter)
    assert 'not available' in capsys.readouterr().out
    assert OscEmitter(CaptureSender(), 'no-such-protocol').protocol == 'legacy'
