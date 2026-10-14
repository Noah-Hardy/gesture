#!/usr/bin/env python3
"""
Live Config Keys (#65)
The config keys a running engine can change without a restart, and how to
validate a value for each. Qt-free and free of mediapipe/cv2: the settings UI
(#64) imports LIVE_KEYS to mark every other key restart-class, and the engine
uses the same table to accept or reject a {"cmd": "set"} command.

How each group takes effect in the engine (src/live_settings.py):

    mediapipe.min_*, hand.min_*   the landmarkers are rebuilt on the processing
                                  thread with the new options (MediaPipe Tasks
                                  options are immutable). Costs a brief pause
                                  in tracking, typically a few hundred ms.
                                  Holistic mode ignores hand.min_detection_confidence
                                  and hand.min_tracking_confidence (the model has
                                  no such options); they are accepted and stored.
    osc.host, osc.port            the sender's UDP client is retargeted; counters
                                  and the queue are untouched
    osc.queue_size                the sender queue is resized in place
    osc.tracking_hold             both debounce windows are updated
    performance.target_fps        the frame pacer's interval
    performance.show_fps          the processors' FPS log line
    display.*, hand.*_color       drawing specs are rebuilt; mirror/draw/background
                                  flags are read per frame

Everything else (camera.*, osc.protocol, mediapipe.pose_model_type,
mediapipe.num_poses, hand.num_hands, performance.force_*/no_holistic/gc_enabled/
max_pending_frames, input mode) is restart-class.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from .config import Config


# ============================================================================
# VALIDATORS
# ============================================================================
def _unit_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number between 0 and 1")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError("must be between 0 and 1")
    return value


def _bool(value):
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _int_range(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError(f"must be a whole number between {low} and {high}")
        value = int(value)
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")
        return value
    return check


def _float_range(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"must be a number between {low} and {high}")
        value = float(value)
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")
        return value
    return check


def _host(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a host name or IP address")
    return value.strip()


def _color(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(isinstance(c, bool) or not isinstance(c, int) or not 0 <= c <= 255 for c in value)):
        raise ValueError("must be three whole numbers between 0 and 255")
    return [int(c) for c in value]


# ============================================================================
# LIVE KEY TABLE
# ============================================================================
_VALIDATORS = {
    # Confidence thresholds - applied by rebuilding the landmarkers
    'mediapipe.min_detection_confidence': _unit_float,
    'mediapipe.min_tracking_confidence': _unit_float,
    'mediapipe.min_pose_presence_confidence': _unit_float,
    'hand.min_detection_confidence': _unit_float,
    'hand.min_presence_confidence': _unit_float,
    'hand.min_tracking_confidence': _unit_float,

    # OSC output
    'osc.host': _host,
    'osc.port': _int_range(1, 65535),
    'osc.queue_size': _int_range(Config.MIN_OSC_QUEUE_SIZE, 100000),
    'osc.tracking_hold': _float_range(0.0, 60.0),

    # Performance
    'performance.target_fps': _int_range(0, 1000),
    'performance.show_fps': _bool,

    # Display (drawing and preview delivery)
    'display.mirror_preview': _bool,
    'display.draw_landmarks': _bool,
    'display.render_in_background': _bool,
    'display.landmark_color': _color,
    'display.connection_color': _color,
    'display.landmark_thickness': _int_range(0, 32),
    'display.landmark_radius': _int_range(0, 32),
    'display.connection_thickness': _int_range(0, 32),
    'display.connection_radius': _int_range(0, 32),
    'hand.left_landmark_color': _color,
    'hand.left_connection_color': _color,
    'hand.right_landmark_color': _color,
    'hand.right_connection_color': _color,
}

#: Every "section.key" the engine applies live
LIVE_KEYS = frozenset(_VALIDATORS)

#: The subset that costs a landmarker rebuild
LANDMARKER_KEYS = frozenset(k for k in LIVE_KEYS
                            if k.startswith(('mediapipe.min_', 'hand.min_')))

#: The subset that changes how landmarks are drawn
DRAWING_KEYS = frozenset(k for k in LIVE_KEYS if k.startswith('display.') or k.startswith('hand.')
                         and k.endswith('_color'))


def is_live(key):
    """True if the running engine can apply this "section.key" without a restart"""
    return key in LIVE_KEYS


#: Sections only the app reads (the engine never sees them), so changing one
#: never needs an engine restart
APP_ONLY_SECTIONS = ('updates',)

#: Individual app-only keys in sections the engine also reads (ui.mode is its)
APP_ONLY_KEYS = ('ui.launch_animation',)


def is_app_only(key):
    """True if only the app reads this "section.key" - no restart, nothing to send"""
    return key in APP_ONLY_KEYS or key.split('.', 1)[0] in APP_ONLY_SECTIONS


def coerce_live_value(key, value):
    """
    Validate and normalize a value for a live key

    Args:
        key: "section.key"
        value: The value from the launcher

    Returns:
        The normalized value (e.g. an int-valued float becomes an int)

    Raises:
        KeyError: key is not a live key
        ValueError: value is not valid for the key; the message says why
    """
    return _VALIDATORS[key](value)
