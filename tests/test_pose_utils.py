"""
pose_utils: the OSC wire format. A silent regression here breaks every
receiver patch downstream, so the landmark_dict/process_landmarks_to_dict
shapes are pinned as golden values.
"""
from types import SimpleNamespace

import numpy as np

from src.pose_utils import (
    LetterboxTransform,
    compact_json,
    landmark_dict,
    letterbox_frame,
    process_landmarks_to_dict,
)


def _landmark(x, y, z, visibility=None):
    ns = SimpleNamespace(x=x, y=y, z=z)
    if visibility is not None:
        ns.visibility = visibility
    return ns


# ----------------------------------------------------------------------------
# landmark_dict / process_landmarks_to_dict (wire format)
# ----------------------------------------------------------------------------
def test_landmark_dict_shape_with_visibility():
    lms = [_landmark(0.123456, 0.654321, 0.111111, visibility=0.987654)]
    d = landmark_dict(lms, 0)
    assert d == {'id': 0, 'x': 0.123, 'y': 0.654, 'z': 0.111, 'visibility': 0.988}


def test_landmark_dict_omits_visibility_when_absent():
    lms = [_landmark(0.1, 0.2, 0.3)]
    d = landmark_dict(lms, 0)
    assert 'visibility' not in d
    assert d == {'id': 0, 'x': 0.1, 'y': 0.2, 'z': 0.3}


def test_landmark_dict_applies_transform():
    # Identity-ish transform: no padding, so to_source_xy/z are pure /scale
    t = LetterboxTransform(scale=2.0, pad_x=0, pad_y=0, proc_w=100, proc_h=100, src_w=100, src_h=100)
    lms = [_landmark(0.5, 0.5, 1.0)]
    d = landmark_dict(lms, 0, transform=t)
    assert d['x'] == 0.5 and d['y'] == 0.5  # pad_x==pad_y==0 -> identity for x/y
    assert d['z'] == 0.5  # z is always /scale regardless of padding


def test_process_landmarks_to_dict_sets_type_and_id_in_order():
    lms = [_landmark(0.1, 0.1, 0.1, visibility=1.0), _landmark(0.2, 0.2, 0.2, visibility=0.5)]
    out = process_landmarks_to_dict(lms, landmark_type='pose_world')
    assert [d['id'] for d in out] == [0, 1]
    assert all(d['type'] == 'pose_world' for d in out)


def test_process_landmarks_to_dict_visibility_none_when_absent():
    out = process_landmarks_to_dict([_landmark(0.1, 0.1, 0.1)])
    assert out[0]['visibility'] is None


def test_compact_json_has_no_whitespace():
    s = compact_json({'a': 1, 'b': [1, 2]})
    assert s == '{"a":1,"b":[1,2]}'


# ----------------------------------------------------------------------------
# letterbox_frame / LetterboxTransform
# ----------------------------------------------------------------------------
def test_letterbox_matching_aspect_needs_no_padding():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)  # 2:1, matches proc 2:1
    img, t = letterbox_frame(frame, proc_w=100, proc_h=50)
    assert img.shape == (50, 100, 3)
    assert t.pad_x == 0 and t.pad_y == 0


def test_letterbox_mismatched_aspect_pads_and_maps_back():
    # Wide source into a square target: padding lands top/bottom
    frame = np.zeros((100, 200, 3), dtype=np.uint8)  # 2:1
    img, t = letterbox_frame(frame, proc_w=100, proc_h=100)
    assert img.shape == (100, 100, 3)
    assert t.pad_y > 0
    assert t.pad_x == 0

    # A point at the exact center of the padded frame maps back to the
    # center of the original source frame
    sx, sy = t.to_source_xy(0.5, 0.5)
    assert abs(sx - 0.5) < 1e-6
    assert abs(sy - 0.5) < 1e-6


def test_letterbox_transform_z_is_scale_only_no_padding_offset():
    t = LetterboxTransform(scale=0.5, pad_x=10, pad_y=20, proc_w=100, proc_h=100, src_w=200, src_h=200)
    assert t.to_source_z(1.0) == 2.0  # 1.0 / 0.5


def test_letterbox_frame_reuses_buffer_when_shape_matches():
    frame = np.full((50, 100, 3), 255, dtype=np.uint8)
    buffer = np.zeros((50, 100, 3), dtype=np.uint8)
    img, _ = letterbox_frame(frame, proc_w=100, proc_h=50, buffer=buffer)
    assert img is buffer
