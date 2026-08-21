#!/usr/bin/env python3
"""
MediaPipe OSC Source Package
Main package initialization and exports
"""

import importlib

# ============================================================================
# LAZY EXPORTS
# ============================================================================
# Importing this package must not eagerly pull in mediapipe/cv2/NDI - those
# only get imported the first time one of these names is actually accessed
# (PEP 562 module __getattr__). Something as innocuous as `from src import
# docs` used to drag in the entire ML stack (~0.45s) as a side effect of this
# file's top-level imports; every consumer of `src.docs` (gui.py,
# help_window.py, updater.py, update_dialog.py) paid that cost even though
# none of them need mediapipe/cv2/NDI to render docs.
_EXPORTS = {
    # OSC Communication
    'ThreadedOSCSender': 'osc_sender',

    # Pose Utilities
    'get_pose_bounds_with_values': 'pose_utils',
    'landmark_dict': 'pose_utils',
    'process_landmarks_to_dict': 'pose_utils',

    # Model Management
    'download_pose_model': 'model_downloader',
    'download_hand_model': 'model_downloader',
    'download_holistic_model': 'model_downloader',

    # Pose Processors
    'PoseProcessor': 'pose_processor',
    'TasksPoseProcessor': 'pose_processor',
    'LegacyPoseProcessor': 'pose_processor',
    'GPUPoseProcessor': 'pose_processor',
    'CPUPoseProcessor': 'pose_processor',

    # Hand Processors
    'HandProcessor': 'hand_processor',
    'TasksHandProcessor': 'hand_processor',
    'LegacyHandProcessor': 'hand_processor',

    # Holistic Processor
    'TasksHolisticProcessor': 'holistic_processor',

    # Configuration
    'Config': 'config',
    'get_config': 'config',

    # NDI Support
    'NDICapture': 'ndi_capture',
    'list_ndi_sources': 'ndi_capture',
    'NDI_AVAILABLE': 'ndi_capture',
}

# ============================================================================
# PUBLIC API
# ============================================================================
__all__ = list(_EXPORTS)


def __getattr__(name):
    """Resolve `src.<name>` on first access by importing its owning submodule"""
    try:
        submodule_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(f'.{submodule_name}', __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache: subsequent access skips __getattr__ entirely
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
