"""
Golden wire-format harness: drives the real pose/hand/holistic processors
with fixed fake MediaPipe results and records every OSC datagram they emit,
byte for byte.

The fixture file (tests/fixtures/osc_golden.json) was captured from the
0.2.1 send code (processors calling ThreadedOSCSender.send_message directly)
before the 0.3.0 protocol refactor. The legacy protocol must keep
reproducing it exactly - test_osc_protocol.py replays these scenarios and
compares bytes.

Regenerate ONLY when the legacy wire format is meant to change (it isn't -
legacy is frozen): `uv run python -m tests.golden_harness --write`
"""
import base64
import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

import numpy as np

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), 'fixtures', 'osc_golden.json')

# Every payload timestamp comes from time.time() inside the processors -
# pinned so the captured bytes are reproducible
FIXED_TIME = 1700000000.123456


# ============================================================================
# FAKE LANDMARKS / RESULTS
# ============================================================================
def fake_landmarks(count, seed, world=False):
    """Deterministic landmark list with values that exercise 3dp rounding"""
    lms = []
    for i in range(count):
        k = seed * 101 + i
        if world:
            x = ((k * 37) % 200 - 100) / 173.0
            y = ((k * 53) % 200 - 100) / 181.0
            z = ((k * 29) % 200 - 100) / 1931.0
        else:
            x = ((k * 37) % 1000) / 997.0
            y = ((k * 53) % 1000) / 991.0
            z = ((k * 29) % 200 - 100) / 1777.0
        vis = ((k * 17) % 1000) / 1009.0
        lms.append(SimpleNamespace(x=x, y=y, z=z, visibility=vis, presence=vis))
    return lms


def _cat(name):
    return [SimpleNamespace(category_name=name)]


def tasks_pose_result(people, world=True):
    return SimpleNamespace(
        pose_landmarks=[fake_landmarks(33, p) for p in range(people)],
        pose_world_landmarks=[fake_landmarks(33, p + 50, world=True) for p in range(people)] if world else [],
    )


def tasks_hand_result(hands):
    """hands: list of handedness labels (None = handedness list missing that entry)"""
    labels = [h for h in hands if h is not None]
    return SimpleNamespace(
        hand_landmarks=[fake_landmarks(21, 10 + i) for i in range(len(hands))],
        hand_world_landmarks=[fake_landmarks(21, 60 + i, world=True) for i in range(len(hands))],
        handedness=[_cat(h) for h in labels],
    )


def holistic_result(pose=True, left=True, right=True, pose_world=True):
    return SimpleNamespace(
        pose_landmarks=fake_landmarks(33, 20) if pose else [],
        pose_world_landmarks=fake_landmarks(33, 70, world=True) if pose and pose_world else [],
        left_hand_landmarks=fake_landmarks(21, 21) if left else [],
        left_hand_world_landmarks=fake_landmarks(21, 71, world=True) if left else [],
        right_hand_landmarks=fake_landmarks(21, 22) if right else [],
        right_hand_world_landmarks=fake_landmarks(21, 72, world=True) if right else [],
    )


def legacy_pose_result(detected):
    if not detected:
        return SimpleNamespace(pose_landmarks=None, pose_world_landmarks=None)
    return SimpleNamespace(
        pose_landmarks=SimpleNamespace(landmark=fake_landmarks(33, 30)),
        pose_world_landmarks=SimpleNamespace(landmark=fake_landmarks(33, 80, world=True)),
    )


def legacy_hand_result(hands):
    if not hands:
        return SimpleNamespace(multi_hand_landmarks=None, multi_handedness=None, multi_hand_world_landmarks=None)
    return SimpleNamespace(
        multi_hand_landmarks=[SimpleNamespace(landmark=fake_landmarks(21, 40 + i)) for i in range(len(hands))],
        multi_handedness=[SimpleNamespace(classification=[SimpleNamespace(label=h)]) for h in hands],
        multi_hand_world_landmarks=[SimpleNamespace(landmark=fake_landmarks(21, 90 + i, world=True)) for i in range(len(hands))],
    )


# ============================================================================
# CAPTURE
# ============================================================================
class CaptureSender:
    """Synchronous stand-in for ThreadedOSCSender that records datagrams"""

    def __init__(self):
        self.dgrams = []

    def send_packet(self, packet):
        self.dgrams.append(packet.dgram)

    def get_stats(self):
        return {'sent': len(self.dgrams), 'dropped': 0, 'queued': 0}


def legacy_emitter(sender):
    """Default output factory: what main.py builds for osc.protocol = legacy"""
    from src.osc_protocol import OscEmitter
    return OscEmitter(sender, 'legacy')


def _no_draw(proc):
    """Landmark drawing is irrelevant to the wire (and needs real protos)"""
    for name in ('_draw_landmarks', '_draw_landmarks_legacy', '_draw_hand_landmarks'):
        setattr(proc, name, lambda *args, **kwargs: None)
    return proc


def _frame(letterbox):
    # 1280x720 into the default 640x480 processing size letterboxes, so the
    # transform is not the identity and normalized coords get remapped
    return np.zeros((720, 1280, 3), np.uint8) if letterbox else np.zeros((480, 640, 3), np.uint8)


def _run_tasks(processor_cls, steps, letterbox, output_factory=legacy_emitter):
    """
    steps: list of ('result', r) - callback delivers r, then a frame runs
                    ('stale',)    - no fresh result, queue not backed up
                    ('skip',)     - frame while MediaPipe is still busy
    """
    sender = CaptureSender()
    proc = _no_draw(processor_cls(output_factory(sender), config=None))
    proc.use_gpu = False
    proc.is_apple_silicon = False
    landmarker = SimpleNamespace(detect_async=lambda image, ts: None)
    frames = []
    for n, step in enumerate(steps):
        kind = step[0]
        if kind == 'result':
            proc._result_callback(step[1], None, n)
        elif kind == 'stale':
            proc.pending_frames = 0
        start = len(sender.dgrams)
        proc.process_frame(_frame(letterbox), landmarker, 'Test', n + 1)
        frames.append(sender.dgrams[start:])
    return frames


def _run_legacy(processor_cls, results, letterbox, output_factory=legacy_emitter):
    sender = CaptureSender()
    proc = _no_draw(processor_cls(output_factory(sender), config=None))
    frames = []
    for r in results:
        ctx = SimpleNamespace(process=lambda image, r=r: r)
        start = len(sender.dgrams)
        proc.process_frame(_frame(letterbox), ctx, 'Test')
        frames.append(sender.dgrams[start:])
    return frames


def scenarios():
    """name -> callable(output_factory) returning a list of per-frame datagram lists"""
    from src.pose_processor import TasksPoseProcessor, LegacyPoseProcessor
    from src.hand_processor import TasksHandProcessor, LegacyHandProcessor
    from src.holistic_processor import TasksHolisticProcessor

    return {
        'tasks_pose_single': lambda f: _run_tasks(TasksPoseProcessor, [
            ('result', tasks_pose_result(1)),
            ('skip',),
            ('stale',),
            ('result', tasks_pose_result(0)),
            ('result', tasks_pose_result(0)),
        ], letterbox=True, output_factory=f),
        'tasks_pose_first_frame_no_results': lambda f: _run_tasks(TasksPoseProcessor, [
            ('stale',),
        ], letterbox=False, output_factory=f),
        'tasks_pose_multi': lambda f: _run_tasks(TasksPoseProcessor, [
            ('result', tasks_pose_result(2)),
            ('result', tasks_pose_result(1)),
            ('result', tasks_pose_result(0)),
        ], letterbox=False, output_factory=f),
        'tasks_pose_no_world': lambda f: _run_tasks(TasksPoseProcessor, [
            ('result', tasks_pose_result(1, world=False)),
        ], letterbox=True, output_factory=f),
        'tasks_hand': lambda f: _run_tasks(TasksHandProcessor, [
            ('result', tasks_hand_result(['Left', 'Right'])),
            ('result', tasks_hand_result(['Left'])),
            ('result', tasks_hand_result([])),
            ('stale',),
            ('result', tasks_hand_result([None])),
            ('result', tasks_hand_result([])),
        ], letterbox=True, output_factory=f),
        'legacy_pose': lambda f: _run_legacy(LegacyPoseProcessor, [
            legacy_pose_result(True),
            legacy_pose_result(False),
            legacy_pose_result(False),
        ], letterbox=True, output_factory=f),
        'legacy_hand': lambda f: _run_legacy(LegacyHandProcessor, [
            legacy_hand_result(['Left', 'Right']),
            legacy_hand_result([]),
            legacy_hand_result([]),
        ], letterbox=False, output_factory=f),
        'holistic': lambda f: _run_tasks(TasksHolisticProcessor, [
            ('result', holistic_result()),
            ('result', holistic_result(right=False)),
            ('result', holistic_result(pose=False, left=False, right=False)),
            ('stale',),
            ('result', holistic_result(pose_world=False, left=False)),
            ('result', holistic_result(pose=False, left=False, right=False)),
        ], letterbox=True, output_factory=f),
    }


def capture(output_factory=legacy_emitter):
    """Run every scenario with time pinned; name -> per-frame datagram lists"""
    with mock.patch('time.time', return_value=FIXED_TIME):
        return {name: run(output_factory) for name, run in scenarios().items()}


def load_fixture():
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    return {
        name: [[base64.b64decode(d['dgram']) for d in frame] for frame in frames]
        for name, frames in data['scenarios'].items()
    }


def _address(dgram):
    return dgram[:dgram.index(b'\x00')].decode()


def write_fixture():
    captured = capture()
    data = {
        'note': 'Frozen 0.2.x OSC wire output (legacy protocol). See tests/golden_harness.py.',
        'scenarios': {
            name: [[{'address': _address(d), 'dgram': base64.b64encode(d).decode()} for d in frame]
                   for frame in frames]
            for name, frames in captured.items()
        },
    }
    os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
    with open(FIXTURE_PATH, 'w') as f:
        json.dump(data, f, indent=1)
        f.write('\n')
    total = sum(len(fr) for frames in captured.values() for fr in frames)
    print(f"Wrote {total} datagrams across {len(captured)} scenarios to {FIXTURE_PATH}")


if __name__ == '__main__':
    if '--write' in sys.argv:
        write_fixture()
    else:
        print(__doc__)
