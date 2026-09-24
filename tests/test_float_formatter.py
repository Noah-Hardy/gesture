"""
FloatFormatter (osc.protocol = float, #52): native OSC float args per
landmark - the addresses Isadora and other numeric receivers bind to.

- addresses and arg type tags (f, never s) for pose, world, bounds, hands
- multi-person: person 0 stays on /pose/lm, later people get /pose/<p>/lm
- bounds order: min_x max_x min_y max_y min_z max_z
- clears: six 0.0s on each bounds address
- every bundle stays within the 1400-byte budget (no IP fragmentation)
"""
import pytest
from pythonosc.osc_bundle import OscBundle
from pythonosc.osc_message import OscMessage
from pythonosc.osc_packet import OscPacket

from src.osc_protocol import (
    FORMATTERS,
    MAX_BUNDLE_BYTES,
    FloatFormatter,
    OscEmitter,
    build_message,
    oversized_messages,
)
from src.pose_utils import LetterboxTransform
from tests import golden_harness as golden
from tests.golden_harness import CaptureSender, fake_landmarks

TRANSFORM = LetterboxTransform(0.5, 0, 60, 640, 480, 1280, 720)


def _messages(dgram):
    if OscBundle.dgram_is_bundle(dgram):
        return [timed.message for timed in OscPacket(dgram).messages]
    return [OscMessage(dgram)]


def _all_messages(sender):
    return [m for d in sender.dgrams for m in _messages(d)]


def _type_tags(address, args):
    dgram = build_message(address, args).dgram
    start = dgram.index(b',')
    return dgram[start + 1:dgram.index(b'\x00', start)].decode()


def test_registered_and_bundled():
    assert FORMATTERS['float'] is FloatFormatter
    assert FloatFormatter.bundled
    assert OscEmitter(CaptureSender(), 'float').protocol == 'float'


def test_single_person_pose_addresses_and_type_tags():
    lms, world = fake_landmarks(33, 0), fake_landmarks(33, 1, world=True)
    out = FloatFormatter().pose(1.0, 0, lms, world, None, 'pose_0')
    addresses = [a for a, _ in out]
    assert addresses == (
        [f'/pose/lm/{n}' for n in range(33)]
        + [f'/pose/world/lm/{n}' for n in range(33)]
        + ['/pose/bounds', '/pose/world_bounds']
    )
    by_address = dict(out)
    assert _type_tags('/pose/lm/0', by_address['/pose/lm/0']) == 'ffff'
    assert _type_tags('/pose/world/lm/0', by_address['/pose/world/lm/0']) == 'fff'
    assert _type_tags('/pose/bounds', by_address['/pose/bounds']) == 'ffffff'
    assert _type_tags('/pose/world_bounds', by_address['/pose/world_bounds']) == 'ffffff'
    assert by_address['/pose/lm/5'] == pytest.approx([lms[5].x, lms[5].y, lms[5].z, lms[5].visibility])
    assert by_address['/pose/world/lm/7'] == pytest.approx([world[7].x, world[7].y, world[7].z])


def test_pose_without_world_sends_no_world_addresses():
    out = FloatFormatter().pose(1.0, 0, fake_landmarks(33, 0), None, None, 'pose_0')
    assert not any('/world' in a for a, _ in out)
    assert out[-1][0] == '/pose/bounds'


def test_bounds_order_is_min_max_per_axis():
    lms = fake_landmarks(33, 3)
    bounds = dict(FloatFormatter().pose(1.0, 0, lms, None, None, 'pose_0'))['/pose/bounds']
    xs, ys, zs = [lm.x for lm in lms], [lm.y for lm in lms], [lm.z for lm in lms]
    assert bounds == pytest.approx([min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)])


def test_letterbox_transform_applies_to_normalized_not_world():
    lms, world = fake_landmarks(33, 0), fake_landmarks(33, 1, world=True)
    out = dict(FloatFormatter().pose(1.0, 0, lms, world, TRANSFORM, 'pose_0'))
    sx, sy = TRANSFORM.to_source_xy(lms[2].x, lms[2].y)
    assert out['/pose/lm/2'][:3] == pytest.approx([sx, sy, TRANSFORM.to_source_z(lms[2].z)])
    assert out['/pose/world/lm/2'] == pytest.approx([world[2].x, world[2].y, world[2].z])
    ys = [TRANSFORM.to_source_xy(lm.x, lm.y)[1] for lm in lms]
    assert out['/pose/bounds'][2:4] == pytest.approx([min(ys), max(ys)])


def test_missing_visibility_is_sent_as_float_zero():
    class Bare:
        x, y, z = 0.1, 0.2, 0.3

    class NoneVis(Bare):
        visibility = None

    out = dict(FloatFormatter().pose(1.0, 0, [Bare(), NoneVis()], None, None, 'pose_0'))
    assert out['/pose/lm/0'][3] == 0.0 and out['/pose/lm/1'][3] == 0.0
    assert _type_tags('/pose/lm/1', out['/pose/lm/1']) == 'ffff'


def test_multi_person_keeps_person_zero_on_the_single_person_addresses():
    fmt = FloatFormatter()
    p0 = [a for a, _ in fmt.pose(1.0, 0, fake_landmarks(33, 0), fake_landmarks(33, 1, world=True), None, 'pose_0')]
    p1 = [a for a, _ in fmt.pose(1.0, 1, fake_landmarks(33, 2), fake_landmarks(33, 3, world=True), None, 'pose_1')]
    assert p0[0] == '/pose/lm/0' and '/pose/bounds' in p0
    assert p1[0] == '/pose/1/lm/0'
    assert '/pose/1/world/lm/32' in p1
    assert p1[-2:] == ['/pose/1/bounds', '/pose/1/world_bounds']
    assert not any(a.startswith('/pose/0/') for a in p0 + p1)


def test_hand_addresses_and_type_tags():
    lms, world = fake_landmarks(21, 10), fake_landmarks(21, 11, world=True)
    left = FloatFormatter().hand(1.0, 'Left', lms, world, TRANSFORM, 'hand_left')
    assert [a for a, _ in left] == (
        [f'/left_hand/lm/{n}' for n in range(21)]
        + [f'/left_hand/world/lm/{n}' for n in range(21)]
        + ['/left_hand/bounds', '/left_hand/world_bounds']
    )
    by_address = dict(left)
    assert _type_tags('/left_hand/lm/0', by_address['/left_hand/lm/0']) == 'fff'
    assert _type_tags('/left_hand/bounds', by_address['/left_hand/bounds']) == 'ffffff'
    # anything not "Left" is the right hand (0.2.x rule)
    for label in ('Right', 'Unknown'):
        right = FloatFormatter().hand(1.0, label, lms, None, None, 'hand_0')
        assert right[0][0] == '/right_hand/lm/0'


def test_pose_clear_zeroes_bounds_for_every_person_seen():
    fmt = FloatFormatter()
    fmt.pose(1.0, 0, fake_landmarks(33, 0), None, None, 'pose_0')
    fmt.pose(1.0, 1, fake_landmarks(33, 1), None, None, 'pose_1')
    cleared = fmt.pose_cleared(2.0)
    assert [a for a, _ in cleared] == ['/pose/bounds', '/pose/world_bounds', '/pose/1/bounds', '/pose/1/world_bounds']
    assert all(args == [0.0] * 6 for _, args in cleared)
    assert all(_type_tags(a, args) == 'ffffff' for a, args in cleared)
    # The next clear only covers person 0 again
    assert [a for a, _ in fmt.pose_cleared(3.0)] == ['/pose/bounds', '/pose/world_bounds']


def test_hand_clears():
    fmt = FloatFormatter()
    assert [a for a, _ in fmt.hand_cleared(1.0, 'Left')] == ['/left_hand/bounds', '/left_hand/world_bounds']
    both = fmt.hand_cleared(1.0, None)
    assert [a for a, _ in both] == [
        '/left_hand/bounds', '/left_hand/world_bounds', '/right_hand/bounds', '/right_hand/world_bounds']
    assert all(args == [0.0] * 6 for _, args in both)


def test_float_status_tracking_heartbeat_are_gesture_native():
    class Clock:
        t = 100.0

        def __call__(self):
            return self.t

    sender = CaptureSender()
    emitter = OscEmitter(sender, 'float', clock=Clock())
    emitter.begin_frame(1.0)
    emitter.pose_status(1)
    emitter.hand_status(2)
    emitter.end_frame()
    emitter.heartbeat(fps=30.0)
    msgs = {m.address: m for m in _all_messages(sender)}
    assert msgs['/gesture/pose/status'].params == [1]
    assert msgs['/gesture/hand/tracking'].params == [2]
    assert b',fiii' in msgs['/gesture/heartbeat'].dgram
    assert all(OscBundle.dgram_is_bundle(d) for d in sender.dgrams)


def test_full_frame_bundles_fit_the_mtu_and_keep_order():
    sender = CaptureSender()
    emitter = OscEmitter(sender, 'float')
    emitter.begin_frame(1.0)
    for person in range(3):
        emitter.pose(person, fake_landmarks(33, person), fake_landmarks(33, person + 5, world=True),
                     TRANSFORM, f'pose_{person}')
    emitter.pose_status(3)
    emitter.hand('Left', fake_landmarks(21, 2), fake_landmarks(21, 3, world=True), TRANSFORM, 'hand_left')
    emitter.hand('Right', fake_landmarks(21, 4), fake_landmarks(21, 5, world=True), TRANSFORM, 'hand_right')
    emitter.hand_status(2)
    emitter.end_frame()
    emitter.clear_all()

    assert sender.dgrams
    for d in sender.dgrams:
        assert OscBundle.dgram_is_bundle(d)
        assert len(d) <= MAX_BUNDLE_BYTES
    msgs = _all_messages(sender)
    assert not oversized_messages(msgs)
    # Nothing but numeric args anywhere in float mode
    for m in msgs:
        assert all(isinstance(p, (int, float)) for p in m.params), m.address
    addresses = [m.address for m in msgs]
    assert addresses.index('/pose/lm/0') < addresses.index('/pose/1/lm/0') < addresses.index('/pose/2/lm/0')
    assert addresses.count('/left_hand/lm/20') == 1


def test_real_processors_run_in_float_mode(capsys):
    """Every golden scenario (pose, hand, holistic, legacy mp.solutions) runs
    through the real processors in float mode without errors or JSON"""
    captured = golden.capture(lambda sender: OscEmitter(sender, 'float'))
    assert 'error' not in capsys.readouterr().out.lower()
    dgrams = [d for frames in captured.values() for frame in frames for d in frame]
    assert dgrams
    for d in dgrams:
        assert len(d) <= MAX_BUNDLE_BYTES
        for m in _messages(d):
            assert all(isinstance(p, (int, float)) for p in m.params), m.address
    addresses = {m.address for d in dgrams for m in _messages(d)}
    assert {'/pose/lm/0', '/pose/1/lm/0', '/left_hand/lm/0', '/right_hand/world/lm/20',
            '/pose/bounds', '/gesture/pose/status'} <= addresses
