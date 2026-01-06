#!/usr/bin/env python3
"""
MediaPipe Model Downloader
Handles automatic downloading of pose and hand landmarker models
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import urllib.request


# ============================================================================
# CONSTANTS
# ============================================================================
# Directory where task models are stored
TASKS_DIR = os.path.join(os.path.dirname(__file__), "tasks")

# Model URLs
POSE_MODEL_URLS = {
    "lite": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "full": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
    "heavy": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/1/pose_landmarker_heavy.task"
}
HAND_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"


# ============================================================================
# MODEL DOWNLOAD FUNCTIONS
# ============================================================================
def get_model_path(model_name):
    """
    Get the full path to a model file in the tasks directory
    
    Args:
        model_name: Name of the model file (e.g., "pose_landmarker_lite.task")
        
    Returns:
        str: Full path to the model file
    """
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
    model_path = get_model_path(model_filename)
    model_url = POSE_MODEL_URLS[model_type]
    
    # Ensure tasks directory exists
    os.makedirs(TASKS_DIR, exist_ok=True)
    
    # Check if model already exists
    if not os.path.exists(model_path):
        print(f"📥 Downloading pose model ({model_type}) from Google...")
        try:
            urllib.request.urlretrieve(model_url, model_path)
            print(f"✅ Downloaded model to {model_path}")
        except Exception as e:
            print(f"❌ Failed to download model: {e}")
            return None
    else:
        print(f"📁 Using existing {model_type} model: {model_path}")
    
    return model_path


def download_hand_model():
    """
    Download the official MediaPipe hand landmarker model if not present
    
    Returns:
        str: Path to the model file, or None if download fails
    """
    model_path = get_model_path("hand_landmarker.task")
    
    # Ensure tasks directory exists
    os.makedirs(TASKS_DIR, exist_ok=True)
    
    # Check if model already exists
    if not os.path.exists(model_path):
        print(f"📥 Downloading hand model from Google...")
        try:
            urllib.request.urlretrieve(HAND_MODEL_URL, model_path)
            print(f"✅ Downloaded model to {model_path}")
        except Exception as e:
            print(f"❌ Failed to download model: {e}")
            return None
    else:
        print(f"📁 Using existing model: {model_path}")
    
    return model_path
