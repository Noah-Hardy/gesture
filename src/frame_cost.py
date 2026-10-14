#!/usr/bin/env python3
"""
Frame Cost

Measures how long each pose model takes per frame on this machine, once, and
caches it in `frame_cost.json` next to config.json. Run from a worker thread
(the Tracking pane's "Measure" button); never at startup. Qt-free.

The numbers are single-frame CPU inference time on a synthetic frame - a guide
for choosing lite / full / heavy, not a benchmark of the live pipeline.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import json
import os
import statistics
import time
from typing import Callable, Dict, Optional

from .config import app_support_dir

MODELS = ('lite', 'full', 'heavy')
WARMUP_FRAMES = 3
MEASURE_FRAMES = 15
FRAME_SIZE = (640, 480)


def cache_path() -> str:
    """Where the measurements are cached."""
    return os.path.join(app_support_dir(), 'frame_cost.json')


def load_cache(path: Optional[str] = None) -> Dict[str, float]:
    """
    Read cached measurements.

    Args:
        path: Cache file (default: next to config.json).

    Returns:
        model -> milliseconds; empty when nothing was measured or the file is unreadable.
    """
    try:
        with open(path or cache_path()) as f:
            data = json.load(f)
        return {k: float(v) for k, v in data.items() if k in MODELS}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_cache(costs: Dict[str, float], path: Optional[str] = None) -> None:
    """Write measurements (best effort)."""
    try:
        with open(path or cache_path(), 'w') as f:
            json.dump(costs, f, indent=2)
    except OSError:
        pass


def measure_model(model: str) -> Optional[float]:
    """
    Median per-frame inference time of one pose model.

    Args:
        model: lite, full or heavy.

    Returns:
        Milliseconds, or None if the model could not be loaded.
    """
    import mediapipe as mp
    import numpy as np

    from .model_downloader import download_pose_model

    path = download_pose_model(model)
    if not path or not os.path.exists(path):
        return None
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=path,
                                          delegate=mp.tasks.BaseOptions.Delegate.CPU),
        running_mode=mp.tasks.vision.RunningMode.IMAGE, num_poses=1)
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (FRAME_SIZE[1], FRAME_SIZE[0], 3), dtype=np.uint8)
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as landmarker:
        times = []
        for i in range(WARMUP_FRAMES + MEASURE_FRAMES):
            t0 = time.perf_counter()
            landmarker.detect(image)
            if i >= WARMUP_FRAMES:
                times.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(times)


def measure_all(progress: Optional[Callable[[str, Optional[float]], None]] = None,
                path: Optional[str] = None) -> Dict[str, float]:
    """
    Measure every model and cache the result.

    Args:
        progress: Called with (model, ms) after each model (ms None on failure).
        path: Cache file override (tests).

    Returns:
        model -> milliseconds for the models that could be measured.
    """
    costs = load_cache(path)
    for model in MODELS:
        try:
            ms = measure_model(model)
        except Exception:
            ms = None
        if ms is not None:
            costs[model] = round(ms, 1)
        if progress:
            progress(model, ms)
    save_cache(costs, path)
    return costs
