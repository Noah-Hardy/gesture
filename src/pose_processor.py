#!/usr/bin/env python3
"""
Pose Processing Module
MediaPipe Tasks pose landmarker with GPU acceleration and multi-pose tracking
"""

# ============================================================================
# IMPORTS
# ============================================================================
import mediapipe as mp

from .landmarker_processor import LandmarkerProcessor, POSE_CONNECTIONS
from .model_downloader import download_pose_model


# ============================================================================
# MEDIAPIPE TASKS POSE PROCESSOR
# ============================================================================
class TasksPoseProcessor(LandmarkerProcessor):
    """
    MediaPipe Tasks pose processor
    Supports GPU acceleration and multi-pose detection
    """

    label = "Pose"

    def __init__(self, osc, show_fps=False, config=None, force_cpu=False, force_gpu=False, is_apple_silicon=None):
        """
        Initialize Tasks pose processor

        Args:
            osc: OscEmitter instance
            show_fps: Boolean to enable FPS display
            config: Configuration object
            force_cpu: Force CPU delegate even if GPU available
            force_gpu: Force GPU delegate (WARNING: memory leak on Apple Silicon)
            is_apple_silicon: Override Apple Silicon detection
        """
        super().__init__(osc, show_fps, config, force_cpu, force_gpu, is_apple_silicon)

        # Pre-build DrawingSpec objects for landmark rendering
        self.refresh_display_specs()

    def refresh_display_specs(self):
        """Rebuild the drawing specs from the display config (also the live-apply path)"""
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

    # ------------------------------------------------------------------------
    # Landmarker hooks
    # ------------------------------------------------------------------------

    @property
    def model_name(self):
        mp_config = self.config.get('mediapipe') if self.config else {}
        return f"pose model ({mp_config.get('pose_model_type', 'lite')})"

    def _download_model(self):
        mp_config = self.config.get('mediapipe') if self.config else {}
        return download_pose_model(mp_config.get('pose_model_type', 'lite'))

    def _landmarker_types(self):
        return mp.tasks.vision.PoseLandmarker, mp.tasks.vision.PoseLandmarkerOptions

    def build_options(self, delegate):
        """Build PoseLandmarkerOptions for the given delegate"""
        mp_config = self.config.get('mediapipe') if self.config else {}
        return mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=self._model_path,
                delegate=delegate
            ),
            running_mode=mp.tasks.vision.RunningMode.LIVE_STREAM,
            num_poses=mp_config.get('num_poses', 1),
            min_pose_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
            min_pose_presence_confidence=mp_config.get('min_pose_presence_confidence', 0.5),
            min_tracking_confidence=mp_config.get('min_tracking_confidence', 0.5),
            result_callback=self._result_callback
        )

    def _publish(self, results, target, timestamp):
        """Send one set of pose messages per detected pose, and draw them"""
        # Snapshot for this call - stable for the duration of process_frame
        transform = self._letterbox_transform

        if results.pose_landmarks:
            self._last_detection_state = True
            # World landmarks are already real-world metres - the
            # emitter never applies the letterbox transform to them
            all_world = getattr(results, 'pose_world_landmarks', None) or []

            # One set of messages per detected pose, on the ordinary /pose/* channels
            for i, pose_landmark in enumerate(results.pose_landmarks):
                world = all_world[i] if i < len(all_world) else None
                self.osc.pose(i, pose_landmark, world, transform, f"pose_{i}", ts=timestamp)

            self.osc.pose_status(len(results.pose_landmarks))

            for pose_landmark in results.pose_landmarks:
                self._draw_landmarks(target, pose_landmark)
        else:
            # Always send status message so receivers know program is running
            self.osc.pose_status(0)
            # Only send empty data once when transitioning from detected to not detected
            if self._last_detection_state:
                self.osc.pose_cleared(ts=timestamp)
                self._last_detection_state = False

    def _draw_results(self, results, target):
        for pose_landmark in results.pose_landmarks or ():
            self._draw_landmarks(target, pose_landmark)

    def _report_idle(self):
        self.osc.pose_status(0)

    # ------------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------------

    def _draw_landmarks(self, image, landmarks):
        """
        Draw pose landmarks on image
        Uses pre-built DrawingSpec objects cached in __init__

        Args:
            image: Image to draw on
            landmarks: Landmark list to draw
        """
        self._draw_connected(image, landmarks, POSE_CONNECTIONS,
                             self._landmark_spec, self._connection_spec)
