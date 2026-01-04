#!/usr/bin/env python3
"""
MediaPipe Model Downloader
Handles automatic downloading of pose landmarker models
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import urllib.request


# ============================================================================
# MODEL DOWNLOAD FUNCTION
# ============================================================================
def download_pose_model():
    """
    Download the official MediaPipe pose landmarker model if not present
    Uses lightweight model optimized for real-time performance
    
    Returns:
        str: Path to the model file, or None if download fails
    """
    # Official Google MediaPipe model URL
    model_url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
    model_path = "pose_landmarker_lite.task"
    
    # Check if model already exists
    if not os.path.exists(model_path):
        print(f"📥 Downloading pose model from Google...")
        try:
            urllib.request.urlretrieve(model_url, model_path)
            print(f"✅ Downloaded model to {model_path}")
        except Exception as e:
            print(f"❌ Failed to download model: {e}")
            return None
    else:
        print(f"📁 Using existing model: {model_path}")
    
    return model_path
