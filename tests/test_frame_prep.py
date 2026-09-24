"""
FramePrep (#36): one letterbox + colour conversion per source frame,
shared by the pose and hand processors in all mode without holistic.
"""
import numpy as np

from src.frame_prep import FramePrep


def bgr_frame(w, h, value=(10, 20, 30)):
    frame = np.empty((h, w, 3), dtype=np.uint8)
    frame[:] = value
    return frame


def test_same_frame_is_prepared_once():
    prep = FramePrep(64, 48)
    frame = bgr_frame(128, 96)
    image_a, transform_a = prep.letterbox(frame)
    rgb_a = prep.rgb()
    image_b, transform_b = prep.letterbox(frame)
    assert image_b is image_a
    assert transform_b == transform_a
    assert prep.rgb() is rgb_a


def test_new_frame_invalidates_the_cache():
    prep = FramePrep(64, 48)
    prep.letterbox(bgr_frame(64, 48, (1, 2, 3)))
    first = prep.rgb().copy()
    prep.letterbox(bgr_frame(64, 48, (4, 5, 6)))
    assert not np.array_equal(prep.rgb(), first)
    assert tuple(prep.rgb()[0, 0]) == (6, 5, 4)


def test_rgb_and_rgba_channel_order():
    prep = FramePrep(32, 24)
    prep.letterbox(bgr_frame(32, 24, (10, 20, 30)))
    assert tuple(prep.rgb()[0, 0]) == (30, 20, 10)
    assert tuple(prep.rgba()[0, 0]) == (30, 20, 10, 255)


def test_letterbox_pads_mismatched_aspect():
    prep = FramePrep(64, 48)
    image, transform = prep.letterbox(bgr_frame(64, 64))
    assert image.shape == (48, 64, 3)
    assert transform.pad_x > 0 and transform.pad_y == 0


def test_unshared_prep_draws_into_the_frame_when_it_can():
    prep = FramePrep(64, 48)
    frame = bgr_frame(64, 48)
    image, _ = prep.letterbox(frame)
    assert image is frame
    assert prep.drawable(image) is image


def test_resize_buffer_is_always_copied_for_drawing():
    prep = FramePrep(64, 48)
    image, _ = prep.letterbox(bgr_frame(128, 96))
    assert prep.drawable(image) is not image


def test_shared_prep_never_hands_out_the_model_input_for_drawing():
    # The first processor's overlay must not end up in the second one's
    # model input, even when no resize is needed and image is the frame
    prep = FramePrep(64, 48)
    prep.shared = True
    frame = bgr_frame(64, 48, (0, 0, 0))
    image, _ = prep.letterbox(frame)
    target = prep.drawable(image)
    target[:] = 255  # "draw a skeleton"
    assert int(prep.rgb().max()) == 0
