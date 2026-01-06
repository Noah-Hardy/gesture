#!/usr/bin/env python3
"""
MediaPipe OSC Source Package
Main package initialization and exports
"""

# ============================================================================
# IMPORTS
# ============================================================================
from .osc_sender import ThreadedOSCSender
from .pose_utils import get_pose_bounds_with_values, landmark_dict, process_landmarks_to_dict
from .model_downloader import download_pose_model, download_hand_model
from .pose_processor import (
    PoseProcessor, 
    TasksPoseProcessor, 
    LegacyPoseProcessor, 
    GPUPoseProcessor, 
    CPUPoseProcessor
)
from .hand_processor import (
    HandProcessor,
    TasksHandProcessor,
    LegacyHandProcessor
)
from .config import Config, get_config
from .ndi_capture import NDICapture, list_ndi_sources, NDI_AVAILABLE


# ============================================================================
# PUBLIC API
# ============================================================================
__all__ = [
    # OSC Communication
    'ThreadedOSCSender',
    
    # Pose Utilities
    'get_pose_bounds_with_values',
    'landmark_dict', 
    'process_landmarks_to_dict',
    
    # Model Management
    'download_pose_model',
    'download_hand_model',
    
    # Pose Processors
    'PoseProcessor',
    'TasksPoseProcessor',
    'LegacyPoseProcessor',
    'GPUPoseProcessor',
    'CPUPoseProcessor',
    
    # Hand Processors
    'HandProcessor',
    'TasksHandProcessor',
    'LegacyHandProcessor',
    
    # Configuration
    'Config',
    'get_config',
    
    # NDI Support
    'NDICapture',
    'list_ndi_sources',
    'NDI_AVAILABLE'
]
