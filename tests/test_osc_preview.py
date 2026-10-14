"""The OSC address map matches what OscEmitter puts on the wire (#68, shared with #66)."""

import re

import pytest

pytest.importorskip('pythonosc')

from src.osc_preview import MODES, _CapturingSender, addresses, capture, payload_sample  # noqa: E402
from src.osc_protocol import OscEmitter  # noqa: E402

PROTOCOLS = ('legacy', 'json', 'float')


class _Lm:
    def __init__(self, i, vis=None):
        self.x, self.y, self.z = 0.1 * (i % 9), 0.05 * i, -0.01 * i
        if vis is not None:
            self.visibility = vis


def _wire(protocol, mode, num_poses=1):
    """Addresses a receiver sees, from an independently built frame."""
    sender = _CapturingSender()
    em = OscEmitter(sender, protocol)
    em.begin_frame(1.0)
    if mode in ('pose', 'all'):
        for p in range(num_poses):
            em.pose(p, [_Lm(i, 0.9) for i in range(33)], [_Lm(i) for i in range(33)], None, f'pose_{p}', ts=1.0)
        em.pose_status(num_poses)
    if mode in ('hand', 'all'):
        for label in ('Left', 'Right'):
            em.hand(label, [_Lm(i) for i in range(21)], [_Lm(i) for i in range(21)], None, 'hand_0', ts=1.0)
        em.hand_status(2)
    em.end_frame()
    em.heartbeat(fps=30.0)
    return [a for a, _ in sender.messages]


def _pattern(address):
    return re.compile('^' + re.escape(address).replace(r'\*', r'\d+').replace('<n>', r'[1-9]\d*') + '$')


@pytest.mark.parametrize('protocol', PROTOCOLS)
@pytest.mark.parametrize('mode', MODES)
def test_address_map_equals_the_wire(protocol, mode):
    rows = addresses(protocol, mode)
    sent = set(_wire(protocol, mode))
    patterns = [_pattern(a) for a, _, _ in rows]
    # every row is something that is sent, and everything sent is in a row
    for (addr, _desc, _rate), pat in zip(rows, patterns):
        assert any(pat.match(s) for s in sent), addr
    for s in sent:
        assert any(p.match(s) for p in patterns), s
    assert len({a for a, _, _ in rows}) == len(rows)


@pytest.mark.parametrize('mode', MODES)
def test_float_poses_after_the_first_get_their_own_family(mode):
    rows = [a for a, _, _ in addresses('float', mode, num_poses=2)]
    assert ('/pose/<n>/lm/*' in rows) == (mode in ('pose', 'all'))
    sent = set(_wire('float', mode, num_poses=2))
    patterns = [_pattern(a) for a in rows]
    assert all(any(p.match(s) for p in patterns) for s in sent)


def test_status_tracking_and_heartbeat_channels_are_listed():
    legacy = {a: (d, r) for a, d, r in addresses('legacy', 'all')}
    assert {'/mp/status', '/hand/status', '/mp/tracking', '/hand/tracking', '/mp/heartbeat'} <= set(legacy)
    modern = {a: (d, r) for a, d, r in addresses('float', 'pose')}
    assert modern['/gesture/heartbeat'][1] == '1 / s'
    assert '/pose/lm/*' in modern and modern['/pose/lm/*'][0].startswith('33')
    assert '/gesture/hand/status' not in modern


def test_payload_sample_comes_from_the_formatter():
    text, note = payload_sample('float', 'all')
    assert text.startswith('/pose/lm/0 ') and 'floats' in note
    text, _ = payload_sample('json', 'all')
    assert text.startswith('/pose/raw "{') and '\\"person\\"' in text
    text, _ = payload_sample('legacy', 'hand')
    assert text.startswith('/left_hand/raw') and 'handedness' in text
    first = capture('float', 'pose')[0]
    assert first[0] == '/pose/lm/0' and len(first[1]) == 4
