#!/usr/bin/env python3
"""
Pose Landmark Utility Functions
Helper functions for processing and formatting landmark data
"""
import json
from typing import NamedTuple

import cv2
import numpy as np


# ============================================================================
# LETTERBOXING (aspect-preserving resize)
# ============================================================================
class LetterboxTransform(NamedTuple):
    """
    Describes how a source frame was resized+padded to fit a proc_w x proc_h
    frame while preserving its aspect ratio, and maps normalized coordinates
    computed on the padded frame back to normalized coordinates in the
    original source frame.

    scale: factor the source frame was resized by (< 1 shrinks, > 1 grows)
    pad_x, pad_y: black-bar padding (pixels) added on each side of the
        resized image to reach proc_w x proc_h
    proc_w, proc_h: dimensions of the padded frame fed to MediaPipe
    src_w, src_h: dimensions of the original, un-padded source frame
    """
    scale: float
    pad_x: int
    pad_y: int
    proc_w: int
    proc_h: int
    src_w: int
    src_h: int

    def to_source_xy(self, nx, ny):
        """
        Map a normalized (x, y) in the padded frame back to a normalized
        (x, y) in the original source frame. When there's no padding (source
        aspect already matches processing aspect), this is the identity.
        """
        if self.pad_x == 0 and self.pad_y == 0:
            return nx, ny
        px, py = nx * self.proc_w, ny * self.proc_h
        sx = (px - self.pad_x) / self.scale
        sy = (py - self.pad_y) / self.scale
        return sx / self.src_w, sy / self.src_h

    def to_source_z(self, z):
        """
        Undo the letterbox scale factor for a landmark's z value. MediaPipe
        documents z as using roughly the same scale as x, so it needs the
        same /scale correction the x/y transform above applies - no padding
        offset applies since z isn't a spatial position.
        """
        return z / self.scale


def letterbox_frame(frame, proc_w, proc_h, buffer=None):
    """
    Resize `frame` to fit within proc_w x proc_h while preserving its aspect
    ratio, padding with black bars (centered) to reach exactly proc_w x proc_h.
    This avoids the coordinate distortion a plain stretch-to-fit resize
    introduces when the source aspect ratio doesn't match proc_w x proc_h.

    Args:
        frame: source BGR image
        proc_w, proc_h: target processing dimensions
        buffer: optional pre-allocated (proc_h, proc_w, 3) array. Reused as
            the resize target when the source aspect already matches
            proc_w x proc_h (no padding needed), mirroring the existing
            resize-buffer pattern. Ignored when padding is required -
            cv2.copyMakeBorder always allocates a fresh array, so there's
            nothing to write into a caller-owned buffer.

    Returns:
        (image, transform) - image is (proc_h, proc_w, 3). transform is a
        LetterboxTransform mapping normalized coords in `image` back to
        normalized coords in the original `frame`.
    """
    h, w = frame.shape[:2]
    scale = min(proc_w / w, proc_h / h)
    new_w, new_h = round(w * scale), round(h * scale)

    if new_w == proc_w and new_h == proc_h:
        # Source aspect already matches processing aspect - plain resize, no padding
        if buffer is None or buffer.shape[0] != proc_h or buffer.shape[1] != proc_w:
            buffer = np.empty((proc_h, proc_w, 3), dtype=np.uint8)
        cv2.resize(frame, (proc_w, proc_h), dst=buffer, interpolation=cv2.INTER_LINEAR)
        transform = LetterboxTransform(scale, 0, 0, proc_w, proc_h, w, h)
        return buffer, transform

    # Resize into a plain contiguous buffer, then pad - copyMakeBorder always
    # allocates a fresh array, so there's no in-place buffer reuse here
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    pad_w, pad_h = proc_w - new_w, proc_h - new_h
    left, top = pad_w // 2, pad_h // 2
    right, bottom = pad_w - left, pad_h - top

    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    transform = LetterboxTransform(scale, left, top, proc_w, proc_h, w, h)
    return padded, transform


# ============================================================================
# JSON SERIALIZATION
# ============================================================================
def compact_json(data):
    """
    Create compact JSON string to minimize memory usage
    Creates new string each time to avoid interning issues
    """
    # Use separators to minimize whitespace
    json_str = json.dumps(data, separators=(',', ':'))
    # Return as bytes to avoid string interning in Python
    return json_str


# ============================================================================
# BOUNDS CALCULATION
# ============================================================================
def get_pose_bounds_with_values(landmarks, transform=None):
    """
    Calculate bounding box of pose landmarks with their values
    Finds the min/max landmarks in x, y, z dimensions

    Args:
        landmarks: List of landmark objects with x, y, z attributes
        transform: Optional LetterboxTransform mapping normalized coords
            from the (possibly letterboxed) processing frame back to the
            source frame. Defaults to None (no-op). The extremes are found
            in processing-frame space (the transform is a monotonic
            per-axis affine map, so it preserves min/max ordering) and only
            the reported values are mapped back to source-frame space.

    Returns:
        Dict with max_x, min_x, max_y, min_y, max_z, min_z landmark data
    """
    max_x = max_y = max_z = float('-inf')
    min_x = min_y = min_z = float('inf')
    max_x_idx = max_y_idx = min_x_idx = min_y_idx = max_z_idx = min_z_idx = -1

    # Find extremes in each dimension
    for idx, landmark in enumerate(landmarks):
        if landmark.x > max_x:
            max_x = landmark.x
            max_x_idx = idx
        if landmark.x < min_x:
            min_x = landmark.x
            min_x_idx = idx
        if landmark.y > max_y:
            max_y = landmark.y
            max_y_idx = idx
        if landmark.y < min_y:
            min_y = landmark.y
            min_y_idx = idx
        if landmark.z > max_z:
            max_z = landmark.z
            max_z_idx = idx
        if landmark.z < min_z:
            min_z = landmark.z
            min_z_idx = idx

    return {
        "max_x": landmark_dict(landmarks, max_x_idx, transform),
        "min_x": landmark_dict(landmarks, min_x_idx, transform),
        "max_y": landmark_dict(landmarks, max_y_idx, transform),
        "min_y": landmark_dict(landmarks, min_y_idx, transform),
        "max_z": landmark_dict(landmarks, max_z_idx, transform),
        "min_z": landmark_dict(landmarks, min_z_idx, transform)
    }


# ============================================================================
# LANDMARK CONVERSION
# ============================================================================
def landmark_dict(landmarks, idx, transform=None):
    """
    Convert a single landmark to dictionary format

    Args:
        landmarks: List of landmark objects
        idx: Index of landmark to convert
        transform: Optional LetterboxTransform mapping normalized coords
            from the (possibly letterboxed) processing frame back to the
            source frame. Defaults to None (no-op).

    Returns:
        Dict with id, x, y, z, and optionally visibility
    """
    lm = landmarks[idx]
    x, y, z = lm.x, lm.y, lm.z
    if transform is not None:
        x, y = transform.to_source_xy(x, y)
        z = transform.to_source_z(z)

    d = {
        "id": idx,
        "x": round(x, 3),
        "y": round(y, 3),
        "z": round(z, 3)
    }

    # Add visibility if available
    if hasattr(lm, "visibility"):
        d["visibility"] = round(lm.visibility, 3)

    return d


def process_landmarks_to_dict(landmarks, landmark_type="pose", transform=None):
    """
    Convert all landmarks to dictionary format for OSC transmission
    Reduces precision to minimize bandwidth usage

    Args:
        landmarks: List of landmark objects
        landmark_type: Type identifier for the landmarks (e.g., "pose", "pose_world")
        transform: Optional LetterboxTransform mapping normalized coords
            from the (possibly letterboxed) processing frame back to the
            source frame. Defaults to None (no-op). Never pass a transform
            for world landmarks - those are already in real-world metres
            and are aspect-independent.

    Returns:
        List of landmark dictionaries
    """
    landmark_list = []
    for idx, landmark in enumerate(landmarks):
        x, y, z = landmark.x, landmark.y, landmark.z
        if transform is not None:
            x, y = transform.to_source_xy(x, y)
            z = transform.to_source_z(z)

        landmark_data = {
            "type": landmark_type,
            "id": idx,
            "x": round(x, 3),
            "y": round(y, 3),
            "z": round(z, 3),
            "visibility": round(landmark.visibility, 3) if hasattr(landmark, "visibility") else None
        }
        landmark_list.append(landmark_data)

    return landmark_list
