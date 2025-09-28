"""
Model downloader utility for MediaPipe pose models.
"""
import os
import urllib.request


def download_pose_model():
    """Download the official MediaPipe pose landmarker model if not present"""
    model_url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
    model_path = "pose_landmarker_lite.task"
    
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
