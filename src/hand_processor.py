#!/usr/bin/env python3
"""
Hand Processing Module
MediaPipe Tasks hand landmarker with GPU acceleration and multi-hand tracking
"""

# ============================================================================
# IMPORTS
# ============================================================================
import mediapipe as mp

from .landmarker_processor import LandmarkerProcessor, HAND_CONNECTIONS
from .model_downloader import download_hand_model


# ============================================================================
# MEDIAPIPE TASKS HAND PROCESSOR
# ============================================================================
class TasksHandProcessor(LandmarkerProcessor):
    """
    MediaPipe Tasks hand processor
    Supports GPU acceleration and multi-hand detection
    """

    label = "Hand"
    backend_suffix = " - Hand"

    def __init__(self, osc, show_fps=False, config=None, force_cpu=False, force_gpu=False, is_apple_silicon=None):
        """
        Initialize Tasks hand processor

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
        self._build_hand_specs()

    # ------------------------------------------------------------------------
    # Landmarker hooks
    # ------------------------------------------------------------------------

    def _download_model(self):
        return download_hand_model()

    def _landmarker_types(self):
        return mp.tasks.vision.HandLandmarker, mp.tasks.vision.HandLandmarkerOptions

    def build_options(self, delegate):
        """Build HandLandmarkerOptions for the given delegate"""
        hand_config = self.config.get('hand') if self.config else {}
        return mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=self._model_path,
                delegate=delegate
            ),
            running_mode=mp.tasks.vision.RunningMode.LIVE_STREAM,
            num_hands=hand_config.get('num_hands', 2),
            min_hand_detection_confidence=hand_config.get('min_detection_confidence', 0.5),
            min_hand_presence_confidence=hand_config.get('min_presence_confidence', 0.5),
            min_tracking_confidence=hand_config.get('min_tracking_confidence', 0.5),
            result_callback=self._result_callback
        )

    @staticmethod
    def _handedness_label(results, index):
        """Handedness category for hand `index`, or "Unknown" when the model gave none"""
        if results.handedness and index < len(results.handedness):
            return results.handedness[index][0].category_name
        return "Unknown"

    def _publish(self, results, target, timestamp):
        """Send one set of messages per detected hand, clear hands that vanished, and draw"""
        # Track which hand prefixes (left_hand/right_hand) are actually sent this
        # frame, resolved via the same rule osc_protocol.hand_prefix uses, so an "Unknown"
        # handedness label can't desync the flags from what was actually sent.
        seen_prefixes = set()
        hand_count = 0

        if results.hand_landmarks:
            # Snapshot for this call - stable for the duration of process_frame
            transform = self._letterbox_transform
            # World landmarks are already real-world metres - the
            # emitter never applies the letterbox transform to them
            all_world = getattr(results, 'hand_world_landmarks', None) or []
            all_handedness = []

            # One set of messages per detected hand, on /left_hand/* or /right_hand/*
            for i, hand_landmark in enumerate(results.hand_landmarks):
                handedness = self._handedness_label(results, i)
                all_handedness.append(handedness)

                world = all_world[i] if i < len(all_world) else None
                seen_prefixes.add("left_hand" if handedness.lower() == "left" else "right_hand")
                hand_count += 1
                self.osc.hand(handedness, hand_landmark, world, transform, f"hand_{i}", ts=timestamp)

            for hand_landmark, handedness in zip(results.hand_landmarks, all_handedness):
                self._draw_landmarks(target, hand_landmark, handedness)

        # Always send status message so receivers know program is running
        self.osc.hand_status(hand_count)

        # Per-hand transition-to-empty clearing: any hand not seen this frame
        # that was tracked last frame gets cleared exactly once. Running this
        # against an empty seen_prefixes set in the no-hands case subsumes the
        # old all-hands-at-once clear, so the two paths can't double-send.
        for hand_prefix, handedness_label, last_state_attr in (
            ("left_hand", "Left", '_last_left_hand_state'),
            ("right_hand", "Right", '_last_right_hand_state'),
        ):
            if hand_prefix in seen_prefixes:
                setattr(self, last_state_attr, True)
            elif getattr(self, last_state_attr):
                self.osc.hand_cleared(handedness_label, ts=timestamp)
                setattr(self, last_state_attr, False)

    def _draw_results(self, results, target):
        for i, hand_landmark in enumerate(results.hand_landmarks or ()):
            self._draw_landmarks(target, hand_landmark, self._handedness_label(results, i))

    def _report_idle(self):
        self.osc.hand_status(0)

    # ------------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------------

    def _draw_landmarks(self, image, landmarks, handedness="Unknown"):
        """
        Draw hand landmarks on image
        Uses pre-built DrawingSpec objects cached in __init__
        Different colors for left and right hands

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
