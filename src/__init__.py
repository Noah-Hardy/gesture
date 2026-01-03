"""
MediaPipe OSC Source Package
"""

from .osc_sender import ThreadedOSCSender
from .pose_utils import get_pose_bounds_with_values, landmark_dict, process_landmarks_to_dict
from .model_downloader import download_pose_model
from .pose_processor import PoseProcessor, TasksPoseProcessor, LegacyPoseProcessor, GPUPoseProcessor, CPUPoseProcessor
from .config import Config, get_config

__all__ = [
    'ThreadedOSCSender',
    'get_pose_bounds_with_values',
    'landmark_dict', 
    'process_landmarks_to_dict',
    'download_pose_model',
    'PoseProcessor',
    'TasksPoseProcessor',
    'LegacyPoseProcessor',
    'GPUPoseProcessor',
    'CPUPoseProcessor',
    'Config',
    'get_config'
]
