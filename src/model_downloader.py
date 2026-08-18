#!/usr/bin/env python3
"""
MediaPipe Model Downloader
Handles automatic downloading of pose and hand landmarker models
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import sys
import urllib.request


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


def download_pose_model(model_type="lite"):
    """
    Download the official MediaPipe pose landmarker model if not present
    
    Args:
        model_type: Type of model to use - "lite", "full", or "heavy" (default: "lite")
    
    Returns:
        str: Path to the model file, or None if download fails
    """
    # Validate model type
    if model_type not in POSE_MODEL_URLS:
        print(f"⚠️  Invalid model type '{model_type}', defaulting to 'lite'")
        model_type = "lite"
    
    model_filename = f"pose_landmarker_{model_type}.task"
    model_url = POSE_MODEL_URLS[model_type]

    # Use an existing model (bundled or previously downloaded) if available
    existing_path = get_model_path(model_filename)
    if os.path.exists(existing_path):
        print(f"📁 Using existing {model_type} model: {existing_path}")
        return existing_path

    # Ensure tasks directory exists
    os.makedirs(TASKS_DIR, exist_ok=True)
    model_path = os.path.join(TASKS_DIR, model_filename)

    print(f"📥 Downloading pose model ({model_type}) from Google...")
    try:
        urllib.request.urlretrieve(model_url, model_path)
        print(f"✅ Downloaded model to {model_path}")
    except Exception as e:
        print(f"❌ Failed to download model: {e}")
        return None

    return model_path


def download_holistic_model():
    """
    Download the official MediaPipe holistic landmarker model if not present

    Returns:
        str: Path to the model file, or None if download fails
    """
    model_filename = "holistic_landmarker.task"

    # Use an existing model (bundled or previously downloaded) if available
    existing_path = get_model_path(model_filename)
    if os.path.exists(existing_path):
        print(f"📁 Using existing model: {existing_path}")
        return existing_path

    # Ensure tasks directory exists
    os.makedirs(TASKS_DIR, exist_ok=True)
    model_path = os.path.join(TASKS_DIR, model_filename)

    print(f"📥 Downloading holistic model from Google...")
    try:
        urllib.request.urlretrieve(HOLISTIC_MODEL_URL, model_path)
        print(f"✅ Downloaded model to {model_path}")
    except Exception as e:
        print(f"❌ Failed to download model: {e}")
        return None

    return model_path


def download_hand_model():
    """
    Download the official MediaPipe hand landmarker model if not present
    
    Returns:
        str: Path to the model file, or None if download fails
    """
    model_filename = "hand_landmarker.task"

    # Use an existing model (bundled or previously downloaded) if available
    existing_path = get_model_path(model_filename)
    if os.path.exists(existing_path):
        print(f"📁 Using existing model: {existing_path}")
        return existing_path

    # Ensure tasks directory exists
    os.makedirs(TASKS_DIR, exist_ok=True)
    model_path = os.path.join(TASKS_DIR, model_filename)

    print(f"📥 Downloading hand model from Google...")
    try:
        urllib.request.urlretrieve(HAND_MODEL_URL, model_path)
        print(f"✅ Downloaded model to {model_path}")
    except Exception as e:
        print(f"❌ Failed to download model: {e}")
        return None

    return model_path
