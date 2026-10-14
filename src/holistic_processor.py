#!/usr/bin/env python3
"""
Holistic Processing Module
Implements MediaPipe Tasks holistic landmarker detection
Detects pose + both hands in a single model pass (single person)
Used for 'all' mode to halve inference work vs separate pose + hand landmarkers
"""

# ============================================================================
# IMPORTS
# ============================================================================
import mediapipe as mp
from mediapipe.framework.formats import landmark_pb2

from .landmarker_processor import LandmarkerProcessor, POSE_CONNECTIONS, HAND_CONNECTIONS
from .model_downloader import download_holistic_model


# ============================================================================
# UPSTREAM BUG WORKAROUND
# ============================================================================
# mediapipe 0.10.21's holistic landmarker aborts the whole process when a
# person is detected but one of the sub-graphs has not produced output yet.
#
# Its live-stream callback only checks whether the FACE stream is empty before
# calling _build_landmarker_result, which then reads all seven landmark streams
# unconditionally. Reading an empty packet fails a C++ ABSL_CHECK inside
# Packet::GetProtoMessageLite ("The packet is empty"), which calls abort() on a
# MediaPipe worker thread - not a Python exception, so nothing can catch it.
#
# In practice this fires the moment a new person enters frame: the face and
# pose streams emit on that frame while the hand streams are still empty.
#
# The fix substitutes an empty proto of the correct type for any empty packet,
# then defers to MediaPipe's own builder, so absent parts arrive as empty lists.
#
# This patch is the reason for the `mediapipe<=0.10.21` pin in pyproject.toml -
# it reaches into 0.10.21's private _holistic module internals, so it is not
# guaranteed to apply cleanly to other versions.
#
# TODO: link the upstream mediapipe issue once filed / remove this patch when
# fixed upstream (and reconsider the mediapipe version pin at that point).
_HOLISTIC_FIX_INSTALLED = False


def _install_holistic_empty_packet_fix():
    """
    Make the holistic landmarker tolerate streams that produced no output

    Safe to call more than once; only the first call patches.

    Returns:
        bool: True if the patch is installed (either just now, or already
            installed by a prior call), False if it could not be installed
            (e.g. the internal mediapipe modules it patches are missing).
            Callers MUST NOT construct a HolisticLandmarker when this
            returns False - doing so re-arms the uncatchable abort() this
            patch exists to prevent.
    """
    global _HOLISTIC_FIX_INSTALLED
    if _HOLISTIC_FIX_INSTALLED:
        return True

    try:
        from mediapipe.python import packet_creator
        from mediapipe.framework.formats import classification_pb2
        from mediapipe.tasks.python.vision import holistic_landmarker as _holistic
    except ImportError as e:
        print(f"⚠️  Could not install holistic empty-packet fix: {e}")
        return False

    # Each output stream and the proto type MediaPipe merges it into
    empty_proto_types = {
        _holistic._FACE_LANDMARKS_STREAM_NAME: landmark_pb2.NormalizedLandmarkList,
        _holistic._POSE_LANDMARKS_STREAM_NAME: landmark_pb2.NormalizedLandmarkList,
        _holistic._POSE_WORLD_LANDMARKS_STREAM_NAME: landmark_pb2.LandmarkList,
        _holistic._LEFT_HAND_LANDMARKS_STREAM_NAME: landmark_pb2.NormalizedLandmarkList,
        _holistic._LEFT_HAND_WORLD_LANDMARKS_STREAM_NAME: landmark_pb2.LandmarkList,
        _holistic._RIGHT_HAND_LANDMARKS_STREAM_NAME: landmark_pb2.NormalizedLandmarkList,
        _holistic._RIGHT_HAND_WORLD_LANDMARKS_STREAM_NAME: landmark_pb2.LandmarkList,
        _holistic._FACE_BLENDSHAPES_STREAM_NAME: classification_pb2.ClassificationList,
    }

    original_build = _holistic._build_landmarker_result

    def build_landmarker_result(output_packets):
        """Replace empty packets with empty protos, then build as usual"""
        sanitized = dict(output_packets)
        for stream_name, proto_type in empty_proto_types.items():
            packet = sanitized.get(stream_name)
            if packet is not None and packet.is_empty():
                sanitized[stream_name] = packet_creator.create_proto(proto_type())
        return original_build(sanitized)

    _holistic._build_landmarker_result = build_landmarker_result
    _HOLISTIC_FIX_INSTALLED = True
    return True




# ============================================================================
# MEDIAPIPE TASKS HOLISTIC PROCESSOR
# ============================================================================
class TasksHolisticProcessor(LandmarkerProcessor):
    """
    MediaPipe Tasks holistic processor
    Single-person pose + hand detection in one model pass
    Publishes on the same OSC channels as the separate pose/hand processors
    """

    label = "Holistic"
    backend_suffix = " - Holistic"

    def __init__(self, osc, show_fps=False, config=None, force_cpu=False, force_gpu=False, is_apple_silicon=None):
        """
        Initialize Tasks holistic processor

        Args:
            osc: OscEmitter instance
            show_fps: Boolean to enable FPS display
            config: Configuration object
            force_cpu: Force CPU delegate even if GPU available
            force_gpu: Force GPU delegate (WARNING: memory leak on Apple Silicon)
            is_apple_silicon: Override Apple Silicon detection
        """
        super().__init__(osc, show_fps, config, force_cpu, force_gpu, is_apple_silicon)

        # Per-hand detection state for transition-to-empty clearing
        self._last_left_hand_state = False
        self._last_right_hand_state = False

        # Pose DrawingSpecs, then hand pairs (left = green, right = red by default)
        self.refresh_display_specs()

    def refresh_display_specs(self):
        """Rebuild the pose and hand drawing specs from the config (also the live-apply path)"""
        display_config = self.config.get('display') if self.config else {}
        self._landmark_spec = self._make_spec(
            display_config.get('landmark_color', [245, 117, 66]),
            display_config.get('landmark_thickness', 1),
            display_config.get('landmark_radius', 2)
        )
        self._connection_spec = self._make_spec(
            display_config.get('connection_color', [245, 66, 230]),
            display_config.get('connection_thickness', 1),
            display_config.get('connection_radius', 1)
        )
        self._build_hand_specs()

    # ------------------------------------------------------------------------
    # Landmarker hooks
    # ------------------------------------------------------------------------

    def _before_setup(self):
        # Must run before a landmarker exists (see the note above the helper).
        # If the patch can't be installed, building a HolisticLandmarker would
        # re-arm the uncatchable C++ abort() it exists to prevent - bail out
        # instead so the caller falls back to separate pose + hand landmarkers.
        if not _install_holistic_empty_packet_fix():
            print("❌ Holistic empty-packet fix could not be installed - skipping holistic tracking")
            print("   Falling back to separate pose + hand landmarkers")
            return False
        return True

    def _download_model(self):
        return download_holistic_model()

    def _landmarker_types(self):
        return mp.tasks.vision.HolisticLandmarker, mp.tasks.vision.HolisticLandmarkerOptions

    def build_options(self, delegate):
        """Build HolisticLandmarkerOptions for the given delegate"""
        mp_config = self.config.get('mediapipe') if self.config else {}
        hand_config = self.config.get('hand') if self.config else {}
        return mp.tasks.vision.HolisticLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=self._model_path,
                delegate=delegate
            ),
            running_mode=mp.tasks.vision.RunningMode.LIVE_STREAM,
            min_pose_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
            min_pose_landmarks_confidence=mp_config.get('min_pose_presence_confidence', 0.5),
            min_hand_landmarks_confidence=hand_config.get('min_presence_confidence', 0.5),
            output_face_blendshapes=False,
            output_segmentation_mask=False,
            result_callback=self._result_callback
        )

    def _publish(self, results, target, timestamp):
        """
        Send pose data on /pose/* channels and hand data on /left_hand/*
        and /right_hand/* channels - same protocol as the separate processors
        """
        # Snapshot for this call - stable for the duration of process_frame
        transform = self._letterbox_transform

        # ------------------------------------------------------------
        # Pose (single person - flat landmark list)
        # ------------------------------------------------------------
        if results.pose_landmarks:
            self._last_detection_state = True
            # World landmarks are already real-world metres - the
            # emitter never applies the letterbox transform to them
            self.osc.pose(0, results.pose_landmarks, results.pose_world_landmarks or None,
                          transform, "pose_0", ts=timestamp)
            self.osc.pose_status(1)

            self._draw_landmarks(target, results.pose_landmarks)
        else:
            # Always send status message so receivers know program is running
            self.osc.pose_status(0)
            # Only send empty data once when transitioning from detected to not detected
            if self._last_detection_state:
                self.osc.pose_cleared(ts=timestamp)
                self._last_detection_state = False

        # ------------------------------------------------------------
        # Hands (holistic provides left/right directly)
        # ------------------------------------------------------------
        hand_count = 0
        for handedness, hand_lms, hand_world_lms, last_state_attr in (
            ("Left", results.left_hand_landmarks,
             results.left_hand_world_landmarks, '_last_left_hand_state'),
            ("Right", results.right_hand_landmarks,
             results.right_hand_world_landmarks, '_last_right_hand_state'),
        ):
            if hand_lms:
                hand_count += 1
                setattr(self, last_state_attr, True)
                self.osc.hand(handedness, hand_lms, hand_world_lms or None, transform,
                              f"hand_{handedness.lower()}", ts=timestamp)

                self._draw_hand_landmarks(target, hand_lms, handedness)
            else:
                # Only send empty data once when this hand transitions to not detected
                if getattr(self, last_state_attr):
                    self.osc.hand_cleared(handedness, ts=timestamp)
                    setattr(self, last_state_attr, False)

        self.osc.hand_status(hand_count)

    def _draw_results(self, results, target):
        if results.pose_landmarks:
            self._draw_landmarks(target, results.pose_landmarks)
        if results.left_hand_landmarks:
            self._draw_hand_landmarks(target, results.left_hand_landmarks, "Left")
        if results.right_hand_landmarks:
            self._draw_hand_landmarks(target, results.right_hand_landmarks, "Right")

    def _report_idle(self):
        self.osc.pose_status(0)
        self.osc.hand_status(0)

    # ------------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------------

    def _draw_landmarks(self, image, landmarks):
        """
        Draw pose landmarks on image

        Args:
            image: Image to draw on
            landmarks: Landmark list to draw
        """
        self._draw_connected(image, landmarks, POSE_CONNECTIONS,
                             self._landmark_spec, self._connection_spec)

    def _draw_hand_landmarks(self, image, landmarks, handedness):
        """
        Draw hand landmarks on image using cached left/right DrawingSpecs

        Args:
            image: Image to draw on
            landmarks: Landmark list to draw
            handedness: "Left" or "Right" hand indicator
        """
        if handedness == "Left":
            landmark_spec, connection_spec = self._left_landmark_spec, self._left_connection_spec
        else:
            landmark_spec, connection_spec = self._right_landmark_spec, self._right_connection_spec
        self._draw_connected(image, landmarks, HAND_CONNECTIONS, landmark_spec, connection_spec)
