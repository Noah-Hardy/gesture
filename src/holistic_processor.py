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
import os
import time
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.framework.formats import landmark_pb2

from .pose_utils import get_pose_bounds_with_values, process_landmarks_to_dict, compact_json
from .model_downloader import download_holistic_model
from .pose_processor import PoseProcessor

# MediaPipe hand connections for drawing
HAND_CONNECTIONS = mp.solutions.hands.HAND_CONNECTIONS


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
_HOLISTIC_FIX_INSTALLED = False


def _install_holistic_empty_packet_fix():
    """
    Make the holistic landmarker tolerate streams that produced no output

    Safe to call more than once; only the first call patches.
    """
    global _HOLISTIC_FIX_INSTALLED
    if _HOLISTIC_FIX_INSTALLED:
        return

    try:
        from mediapipe.python import packet_creator
        from mediapipe.framework.formats import classification_pb2
        from mediapipe.tasks.python.vision import holistic_landmarker as _holistic
    except ImportError as e:
        print(f"⚠️  Could not install holistic empty-packet fix: {e}")
        return

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


# ============================================================================
# MEDIAPIPE TASKS HOLISTIC PROCESSOR
# ============================================================================
class TasksHolisticProcessor(PoseProcessor):
    """
    MediaPipe Tasks holistic processor
    Single-person pose + hand detection in one model pass
    Publishes on the same OSC channels as the separate pose/hand processors
    """

    def __init__(self, osc_sender, show_fps=False, config=None, force_cpu=False, force_gpu=False, is_apple_silicon=None):
        """
        Initialize Tasks holistic processor

        Args:
            osc_sender: ThreadedOSCSender instance
            show_fps: Boolean to enable FPS display
            config: Configuration object
            force_cpu: Force CPU delegate even if GPU available
            force_gpu: Force GPU delegate (WARNING: memory leak on Apple Silicon)
            is_apple_silicon: Override Apple Silicon detection
        """
        super().__init__(osc_sender, show_fps, config)
        self.force_cpu = force_cpu
        self.force_gpu = force_gpu
        from .pose_processor import IS_APPLE_SILICON
        self.is_apple_silicon = is_apple_silicon if is_apple_silicon is not None else IS_APPLE_SILICON
        self.use_gpu = False

        # Per-hand detection state for transition-to-empty clearing
        self._last_left_hand_state = False
        self._last_right_hand_state = False

        # Pre-build hand DrawingSpec pairs (left = green, right = red by default)
        display_config = config.get('display') if config else {}
        hand_config = config.get('hand') if config else {}
        landmark_thickness = display_config.get('landmark_thickness', 1)
        landmark_radius = display_config.get('landmark_radius', 2)
        connection_thickness = display_config.get('connection_thickness', 1)
        connection_radius = display_config.get('connection_radius', 1)
        self._left_landmark_spec = mp.solutions.drawing_utils.DrawingSpec(
            color=tuple(hand_config.get('left_landmark_color', [0, 255, 0])),
            thickness=landmark_thickness, circle_radius=landmark_radius
        )
        self._left_connection_spec = mp.solutions.drawing_utils.DrawingSpec(
            color=tuple(hand_config.get('left_connection_color', [0, 200, 0])),
            thickness=connection_thickness, circle_radius=connection_radius
        )
        self._right_landmark_spec = mp.solutions.drawing_utils.DrawingSpec(
            color=tuple(hand_config.get('right_landmark_color', [255, 0, 0])),
            thickness=landmark_thickness, circle_radius=landmark_radius
        )
        self._right_connection_spec = mp.solutions.drawing_utils.DrawingSpec(
            color=tuple(hand_config.get('right_connection_color', [200, 0, 0])),
            thickness=connection_thickness, circle_radius=connection_radius
        )

    # ------------------------------------------------------------------------
    # OSC hand data transmission (same channels as HandProcessor)
    # ------------------------------------------------------------------------

    def send_hand_data(self, hand_landmarks, hand_world_landmarks, handedness, timestamp):
        """Send hand data via OSC (single hand)"""
        hand_prefix = "left_hand" if handedness.lower() == "left" else "right_hand"

        if hand_landmarks:
            osc_payload = {
                "timestamp": timestamp,
                "handedness": handedness,
                "landmarks": hand_landmarks
            }
            self.osc_sender.send_message(f"/{hand_prefix}/raw", compact_json(osc_payload))

        if hand_world_landmarks:
            world_payload = {
                "timestamp": timestamp,
                "handedness": handedness,
                "landmarks": hand_world_landmarks
            }
            self.osc_sender.send_message(f"/{hand_prefix}/world", compact_json(world_payload))

    def send_hand_bounds_data(self, landmarks, world_landmarks, handedness):
        """Send bounding box data via OSC (single hand)"""
        hand_prefix = "left_hand" if handedness.lower() == "left" else "right_hand"

        if landmarks:
            bounds = get_pose_bounds_with_values(landmarks)
            self.osc_sender.send_message(f"/{hand_prefix}/bounds", compact_json(bounds))

        if world_landmarks:
            world_bounds = get_pose_bounds_with_values(world_landmarks)
            self.osc_sender.send_message(f"/{hand_prefix}/world_bounds", compact_json(world_bounds))

    def send_empty_single_hand_data(self, handedness, timestamp):
        """Send empty data for one hand to clear stale data on receiving machine"""
        hand_prefix = "left_hand" if handedness.lower() == "left" else "right_hand"
        empty_payload = {
            "timestamp": timestamp,
            "handedness": handedness,
            "landmarks": []
        }
        self.osc_sender.send_message(f"/{hand_prefix}/raw", compact_json(empty_payload))
        self.osc_sender.send_message(f"/{hand_prefix}/world", compact_json(empty_payload))
        self.osc_sender.send_message(f"/{hand_prefix}/bounds", compact_json({}))
        self.osc_sender.send_message(f"/{hand_prefix}/world_bounds", compact_json({}))

    # ------------------------------------------------------------------------
    # Async result callback
    # ------------------------------------------------------------------------

    def _result_callback(self, result, output_image, timestamp_ms):
        """
        Callback for async holistic detection results from MediaPipe Tasks
        Runs on MediaPipe's worker thread - state updates guarded by lock
        Note: We only store the result, not the output_image to avoid memory leaks
        """
        with self._results_lock:
            self.results = result
            self._has_fresh_results = True
            self.pending_frames = max(0, self.pending_frames - 1)
        # Explicitly don't store output_image - it's not needed and causes memory leaks
        del output_image

    # ------------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------------

    def setup_processor(self):
        """Setup MediaPipe Tasks holistic processor with GPU/CPU fallback"""
        try:
            # Must run before a landmarker exists (see the note above the helper)
            _install_holistic_empty_packet_fix()

            # Import MediaPipe Tasks API
            BaseOptions = mp.tasks.BaseOptions
            HolisticLandmarker = mp.tasks.vision.HolisticLandmarker
            HolisticLandmarkerOptions = mp.tasks.vision.HolisticLandmarkerOptions
            VisionRunningMode = mp.tasks.vision.RunningMode

            # Get configuration sections
            mp_config = self.config.get('mediapipe') if self.config else {}
            hand_config = self.config.get('hand') if self.config else {}

            # Download model if needed
            model_path = download_holistic_model()

            if not model_path or not os.path.exists(model_path):
                print("❌ Holistic model file not available")
                return None, None, None, False

            # Determine GPU/CPU delegate strategy
            if self.force_cpu:
                print("🔧 Forced CPU delegate via command line")
                use_gpu_delegate = False
            elif self.force_gpu:
                print("⚠️  Forced GPU delegate via command line (WARNING: known memory leak on Apple Silicon)")
                use_gpu_delegate = True
            elif self.is_apple_silicon:
                print("🍎 Apple Silicon detected: Using CPU delegate (GPU has known memory leak)")
                use_gpu_delegate = False
            else:
                use_gpu_delegate = True

            def build_options(delegate):
                return HolisticLandmarkerOptions(
                    base_options=BaseOptions(
                        model_asset_path=model_path,
                        delegate=delegate
                    ),
                    running_mode=VisionRunningMode.LIVE_STREAM,
                    min_pose_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
                    min_pose_landmarks_confidence=mp_config.get('min_pose_presence_confidence', 0.5),
                    min_hand_landmarks_confidence=hand_config.get('min_presence_confidence', 0.5),
                    output_face_blendshapes=False,
                    output_segmentation_mask=False,
                    result_callback=self._result_callback
                )

            landmarker = None
            backend_name = None

            # Try GPU delegate first (unless forced to CPU or on Apple Silicon)
            if use_gpu_delegate:
                print("🎯 Attempting GPU delegate for holistic tracking...")
                try:
                    landmarker = HolisticLandmarker.create_from_options(
                        build_options(BaseOptions.Delegate.GPU)
                    )
                    backend_name = "GPU (MediaPipe Tasks - Holistic)"
                    self.use_gpu = True
                    print("✅ GPU delegate initialized successfully for holistic tracking")
                    if self.is_apple_silicon:
                        print("   Using SRGBA image format for Apple Silicon Metal compatibility")
                except Exception as gpu_error:
                    print(f"⚠️  GPU delegate failed during initialization: {gpu_error}")
                    landmarker = None

            # Fallback to CPU delegate if GPU failed or was not attempted
            if landmarker is None:
                print("🔄 Using CPU delegate for holistic tracking...")
                try:
                    landmarker = HolisticLandmarker.create_from_options(
                        build_options(BaseOptions.Delegate.CPU)
                    )
                    backend_name = "CPU (MediaPipe Tasks - Holistic)"
                    self.use_gpu = False
                    print("✅ CPU delegate initialized successfully for holistic tracking")
                except Exception as cpu_error:
                    print(f"❌ CPU delegate also failed: {cpu_error}")
                    return None, None, None, False

            window_title = "MediaPipe Tasks Holistic Detection"
            print(f"✅ Successfully initialized {backend_name}")
            return landmarker, backend_name, window_title, True

        except ImportError as e:
            print(f"⚠️  MediaPipe Tasks Holistic not available: {e}")
            return None, None, None, False
        except Exception as e:
            print(f"❌ Failed to initialize MediaPipe Tasks Holistic: {e}")
            return None, None, None, False

    # ------------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------------

    def process_frame(self, frame, landmarker, backend_name, timestamp_counter):
        """
        Process a single frame with the MediaPipe Tasks holistic landmarker
        Sends pose data on /pose/* channels and hand data on /left_hand/*
        and /right_hand/* channels — same protocol as the separate processors

        Args:
            frame: Input frame from camera/NDI
            landmarker: MediaPipe HolisticLandmarker instance
            backend_name: Backend name for FPS display
            timestamp_counter: Frame counter for async processing

        Returns:
            Annotated frame with landmarks drawn
        """
        try:
            if frame is None or frame.size == 0:
                return frame

            # Always resize frame for consistent display, regardless of processing
            proc_width = self._proc_width
            proc_height = self._proc_height

            h, w = frame.shape[:2]
            if w != proc_width or h != proc_height:
                if (self._resize_buffer is None or
                    self._resize_buffer.shape[0] != proc_height or
                    self._resize_buffer.shape[1] != proc_width):
                    self._resize_buffer = np.empty((proc_height, proc_width, 3), dtype=np.uint8)

                cv2.resize(frame, (proc_width, proc_height), dst=self._resize_buffer, interpolation=cv2.INTER_LINEAR)
                image = self._resize_buffer
            else:
                image = frame

            # Check if MediaPipe's async queue is backing up - skip frame if too many pending
            if self.pending_frames >= self.max_pending_frames:
                self.skipped_frames += 1
                self.update_fps(backend_name)
                return image.copy() if image is self._resize_buffer else image

            # Convert to RGB for MediaPipe using pre-allocated buffer
            if (self._rgb_buffer is None or
                self._rgb_buffer.shape[0] != image.shape[0] or
                self._rgb_buffer.shape[1] != image.shape[1]):
                self._rgb_buffer = np.empty((image.shape[0], image.shape[1], 3), dtype=np.uint8)

            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=self._rgb_buffer)
            rgb_frame = self._rgb_buffer

            # On Apple Silicon with GPU, use SRGBA format for Metal compatibility
            if self.is_apple_silicon and self.use_gpu:
                rgba_frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2RGBA)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGBA, data=rgba_frame)
            else:
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

            # Process with MediaPipe Tasks (async)
            landmarker.detect_async(mp_image, timestamp_counter)
            with self._results_lock:
                self.pending_frames += 1

            del mp_image

            timestamp = time.time()

            # Convert RGB back to BGR for OpenCV display - reuse resize buffer if available
            if self._resize_buffer is not None and self._resize_buffer.shape == rgb_frame.shape:
                cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR, dst=self._resize_buffer)
                image = self._resize_buffer
            else:
                image = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)

            # Atomically check-and-take fresh results from the callback thread
            fresh_results = None
            with self._results_lock:
                if self._has_fresh_results and self.results is not None:
                    fresh_results = self.results
                    self.results = None
                    self._has_fresh_results = False

            if fresh_results is not None:
                # Keep for stale drawing on frames before the next callback lands (main thread only)
                self._display_results = fresh_results

                # ------------------------------------------------------------
                # Pose (single person - flat landmark list)
                # ------------------------------------------------------------
                pose_detected = bool(fresh_results.pose_landmarks)
                if pose_detected:
                    self._last_detection_state = True
                    pose_landmarks = process_landmarks_to_dict(fresh_results.pose_landmarks, "pose_0")
                    pose_world_landmarks = []
                    if fresh_results.pose_world_landmarks:
                        pose_world_landmarks = process_landmarks_to_dict(fresh_results.pose_world_landmarks, "pose_world_0")

                    self.send_pose_data(pose_landmarks, pose_world_landmarks, timestamp)
                    self.send_bounds_data(
                        fresh_results.pose_landmarks,
                        fresh_results.pose_world_landmarks if pose_world_landmarks else None
                    )
                    self.osc_sender.send_message("/mp/status", compact_json({"status": 1}))

                    self._draw_landmarks(image, fresh_results.pose_landmarks)

                    del pose_landmarks
                    del pose_world_landmarks
                else:
                    # Always send status message so receivers know program is running
                    self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))
                    # Only send empty data once when transitioning from detected to not detected
                    if self._last_detection_state:
                        self.send_empty_data(timestamp)
                        self._last_detection_state = False

                # ------------------------------------------------------------
                # Hands (holistic provides left/right directly)
                # ------------------------------------------------------------
                hand_count = 0
                for handedness, hand_lms, hand_world_lms, last_state_attr in (
                    ("Left", fresh_results.left_hand_landmarks,
                     fresh_results.left_hand_world_landmarks, '_last_left_hand_state'),
                    ("Right", fresh_results.right_hand_landmarks,
                     fresh_results.right_hand_world_landmarks, '_last_right_hand_state'),
                ):
                    if hand_lms:
                        hand_count += 1
                        setattr(self, last_state_attr, True)
                        hand_landmarks = process_landmarks_to_dict(hand_lms, f"hand_{handedness.lower()}")
                        hand_world_landmarks = None
                        if hand_world_lms:
                            hand_world_landmarks = process_landmarks_to_dict(hand_world_lms, f"hand_world_{handedness.lower()}")

                        self.send_hand_data(hand_landmarks, hand_world_landmarks, handedness, timestamp)
                        self.send_hand_bounds_data(hand_lms, hand_world_lms if hand_world_landmarks else None, handedness)

                        self._draw_hand_landmarks(image, hand_lms, handedness)

                        del hand_landmarks
                        del hand_world_landmarks
                    else:
                        # Only send empty data once when this hand transitions to not detected
                        if getattr(self, last_state_attr):
                            self.send_empty_single_hand_data(handedness, timestamp)
                            setattr(self, last_state_attr, False)

                self.osc_sender.send_message("/hand/status", compact_json({"status": hand_count}))
            elif self._display_results is not None:
                # We have results but they're stale (already processed), just draw landmarks
                if self._display_results.pose_landmarks:
                    self._draw_landmarks(image, self._display_results.pose_landmarks)
                if self._display_results.left_hand_landmarks:
                    self._draw_hand_landmarks(image, self._display_results.left_hand_landmarks, "Left")
                if self._display_results.right_hand_landmarks:
                    self._draw_hand_landmarks(image, self._display_results.right_hand_landmarks, "Right")
                # No fresh detection this frame - status 0 signals no actively tracked person
                self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))
                self.osc_sender.send_message("/hand/status", compact_json({"status": 0}))
            else:
                # No results yet - still send status so receivers know program is running
                self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))
                self.osc_sender.send_message("/hand/status", compact_json({"status": 0}))

            # Clear intermediate frames to free memory
            del rgb_frame
            if 'rgba_frame' in locals():
                del rgba_frame

            self.update_fps(backend_name)
            return image

        except Exception as e:
            print(f"⚠️  Holistic frame processing error: {e}")
            with self._results_lock:
                self.results = None
                self._has_fresh_results = False
            self._display_results = None
            return frame

    # ------------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------------

    def _draw_hand_landmarks(self, image, landmarks, handedness):
        """
        Draw hand landmarks on image using cached left/right DrawingSpecs

        Args:
            image: Image to draw on
            landmarks: Landmark list to draw
            handedness: "Left" or "Right" hand indicator
        """
        if handedness == "Left":
            landmark_spec = self._left_landmark_spec
            connection_spec = self._left_connection_spec
        else:
            landmark_spec = self._right_landmark_spec
            connection_spec = self._right_connection_spec

        hand_landmarks_proto = landmark_pb2.NormalizedLandmarkList()
        hand_landmarks_proto.landmark.extend([
            landmark_pb2.NormalizedLandmark(x=landmark.x, y=landmark.y, z=landmark.z)
            for landmark in landmarks
        ])

        mp.solutions.drawing_utils.draw_landmarks(
            image,
            hand_landmarks_proto,
            HAND_CONNECTIONS,
            landmark_spec,
            connection_spec
        )
