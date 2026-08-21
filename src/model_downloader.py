#!/usr/bin/env python3
"""
MediaPipe Model Downloader
Handles automatic downloading of pose and hand landmarker models
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import shutil
import sys
import urllib.request

from src.net import ssl_context

# urlopen() goes through the process-wide opener, so installing one
# HTTPSHandler here fixes TLS verification for every call in this module
# without touching each call site. See src/net.py for why the default
# context is broken inside the frozen app bundle.
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()))
)


# ============================================================================
# PATH RESOLUTION
# ============================================================================
def _bundled_tasks_dir() -> str:
    """Read-only directory holding models shipped with the app."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'src', 'tasks')
    return os.path.join(os.path.dirname(__file__), 'tasks')


def _writable_tasks_dir() -> str:
    """Directory models can be downloaded into."""
    if getattr(sys, 'frozen', False):
        d = os.path.join(os.path.expanduser('~/Library/Application Support'), 'mp-osc', 'models')
        os.makedirs(d, exist_ok=True)
        return d
    return _bundled_tasks_dir()


# ============================================================================
# CONSTANTS
# ============================================================================
# Directory where downloaded task models are stored
TASKS_DIR = _writable_tasks_dir()

# Model URLs
POSE_MODEL_URLS = {
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task"
}
HAND_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
HOLISTIC_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/holistic_landmarker/holistic_landmarker/float16/1/holistic_landmarker.task"


# ============================================================================
# MODEL DOWNLOAD FUNCTIONS
# ============================================================================
def get_model_path(model_name):
    """
    Get the full path to a model file, preferring a bundled model

    Args:
        model_name: Name of the model file (e.g., "pose_landmarker_lite.task")

    Returns:
        str: Full path to the model file (bundled if present, else download location)
    """
    bundled = os.path.join(_bundled_tasks_dir(), model_name)
    if os.path.exists(bundled):
        return bundled
    return os.path.join(TASKS_DIR, model_name)


def _download_model(model_filename, model_url, label):
    """
    Download one model to TASKS_DIR if not already present, atomically.

    Downloads to a <filename>.<pid>.tmp sibling first and verifies its size
    against the response's Content-Length (when the server sends one)
    before os.replace-ing it into place. Without this, an interrupted
    urlretrieve() straight to the final path used to leave a truncated
    .task file that get_model_path's os.path.exists guard then treated as
    a valid cached model forever, with no way out but deleting it by hand.

    No checksum: Google publishes no .sha256 (or similar) sibling for these
    URLs, so any hash here would be one we compute and hardcode ourselves -
    and it would hard-fail every user the moment upstream ever republishes
    the same file. Size-verified + atomic is the fix for the actual bug
    (a truncated file cached forever); a pinned hash is a separate,
    heavier guarantee this doesn't attempt.

    Args:
        model_filename: Name of the model file (e.g., "hand_landmarker.task")
        model_url: URL to download it from
        label: Human-readable name for progress/error messages (e.g., "hand")

    Returns:
        str: Path to the model file, or None if download fails
    """
    existing_path = get_model_path(model_filename)
    if os.path.exists(existing_path):
        print(f"📁 Using existing {label} model: {existing_path}")
        return existing_path

    os.makedirs(TASKS_DIR, exist_ok=True)
    model_path = os.path.join(TASKS_DIR, model_filename)
    tmp_path = f"{model_path}.{os.getpid()}.tmp"

    print(f"📥 Downloading {label} model from Google...")
    try:
        with urllib.request.urlopen(model_url) as response:
            expected_size = response.headers.get('Content-Length')
            expected_size = int(expected_size) if expected_size is not None else None
            with open(tmp_path, 'wb') as f:
                shutil.copyfileobj(response, f)

        actual_size = os.path.getsize(tmp_path)
        if expected_size is not None and actual_size != expected_size:
            raise OSError(
                f"incomplete download: got {actual_size} bytes, expected {expected_size}")

        os.replace(tmp_path, model_path)
        print(f"✅ Downloaded model to {model_path}")
        return model_path
    except Exception as e:
        print(f"❌ Failed to download model: {e}")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def download_pose_model(model_type="lite"):
    """
    Download the official MediaPipe pose landmarker model if not present

    Args:
        model_type: Type of model to use - "lite", "full", or "heavy" (default: "lite")

    Returns:
        str: Path to the model file, or None if download fails
    """
    if model_type not in POSE_MODEL_URLS:
        print(f"⚠️  Invalid model type '{model_type}', defaulting to 'lite'")
        model_type = "lite"

    return _download_model(
        f"pose_landmarker_{model_type}.task", POSE_MODEL_URLS[model_type], f"pose ({model_type})")


def download_holistic_model():
    """
    Download the official MediaPipe holistic landmarker model if not present

    Returns:
        str: Path to the model file, or None if download fails
    """
    return _download_model("holistic_landmarker.task", HOLISTIC_MODEL_URL, "holistic")


def download_hand_model():
    """
    Download the official MediaPipe hand landmarker model if not present

    Returns:
        str: Path to the model file, or None if download fails
    """
    return _download_model("hand_landmarker.task", HAND_MODEL_URL, "hand")
