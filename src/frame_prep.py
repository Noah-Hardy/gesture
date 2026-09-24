#!/usr/bin/env python3
"""
Frame Preparation Module
Letterbox + BGR->RGB(A) conversion for one source frame, computed once and
shared by every processor that reads that frame
"""

# ============================================================================
# IMPORTS
# ============================================================================
import cv2
import numpy as np

from .pose_utils import LetterboxTransform, letterbox_frame


# ============================================================================
# FRAME PREP CLASS
# ============================================================================
class FramePrep:
    """
    Per-frame cache of the model input for a fixed processing size

    Every processor used to letterbox and colour-convert the source frame
    itself, so `all --no-holistic` paid for two resizes and two (four on
    Apple Silicon GPU) full-frame conversions of the same pixels (#36).
    main.py hands the pose and hand processors one shared FramePrep; the
    first to see a frame does the work and the second reuses it.

    Keyed on the frame object's identity: cv2.VideoCapture.read() and
    NDICapture.read() both return a freshly allocated array per call, and
    the cache holds a reference to the frame, so its id can't be recycled
    while cached.
    """

    def __init__(self, proc_width, proc_height):
        self.proc_width = proc_width
        self.proc_height = proc_height
        # Set by PoseProcessor.share_frame_prep - see drawable()
        self.shared = False
        self._resize_buffer = None
        self._rgb_buffer = None
        self._frame = None
        self._image = None
        self._rgb = None
        self._rgba = None
        # Identity until the first letterbox
        self.transform = LetterboxTransform(
            1.0, 0, 0, proc_width, proc_height, proc_width, proc_height
        )

    def letterbox(self, frame):
        """
        Letterbox `frame` to the processing size (cached per frame)

        Returns:
            (image, transform) - image is the clean inference input. Never
            draw into it directly: it may be `frame` itself, the reused
            resize buffer, or another processor's model input - use
            drawable()
        """
        if frame is self._frame:
            return self._image, self.transform

        self._frame = frame
        self._rgb = None
        self._rgba = None

        proc_width, proc_height = self.proc_width, self.proc_height
        h, w = frame.shape[:2]
        if w != proc_width or h != proc_height:
            # Letterbox instead of stretching: preserves the source aspect
            # ratio (padding with black bars) so normalized coordinates
            # sent over OSC stay correct relative to the true source frame
            image, transform = letterbox_frame(frame, proc_width, proc_height, self._resize_buffer)
            if transform.pad_x == 0 and transform.pad_y == 0:
                # No padding needed - image is the reusable resize buffer
                self._resize_buffer = image
        else:
            image = frame
            transform = LetterboxTransform(1.0, 0, 0, proc_width, proc_height, proc_width, proc_height)

        self._image = image
        self.transform = transform
        return image, transform

    def drawable(self, image):
        """
        An array a processor may draw its overlay into

        A copy when `image` is the resize buffer (the next frame overwrites
        it) or when this prep is shared - the other processor may not have
        taken its RGB copy yet, and drawing into the shared image would
        paint one model's skeleton into the other's input.
        """
        if self.shared or image is self._resize_buffer:
            return image.copy()
        return image

    def rgb(self):
        """RGB copy of the current letterboxed frame (converted once per frame)"""
        if self._rgb is None:
            image = self._image
            if (self._rgb_buffer is None or
                self._rgb_buffer.shape[0] != image.shape[0] or
                self._rgb_buffer.shape[1] != image.shape[1]):
                self._rgb_buffer = np.empty((image.shape[0], image.shape[1], 3), dtype=np.uint8)
            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=self._rgb_buffer)
            self._rgb = self._rgb_buffer
        return self._rgb

    def rgba(self):
        """
        RGBA copy of the current frame (converted once per frame)

        On Apple Silicon with GPU, MediaPipe needs SRGBA - the Metal GPU
        buffer doesn't support 3-channel SRGB. Converted straight from BGR,
        so the GPU path is one conversion instead of the old two.
        """
        if self._rgba is None:
            self._rgba = cv2.cvtColor(self._image, cv2.COLOR_BGR2RGBA)
        return self._rgba
