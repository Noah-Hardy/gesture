#!/usr/bin/env python3
"""
Pose Processing Module
Implements both MediaPipe Tasks (modern) and Legacy pose detection
Supports GPU acceleration and multi-pose tracking
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import time
import platform
import gc
import threading
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.framework.formats import landmark_pb2

from .pose_utils import get_pose_bounds_with_values, process_landmarks_to_dict, compact_json, letterbox_frame, LetterboxTransform
from .model_downloader import download_pose_model

# Optional psutil import for memory monitoring
try:
    import psutil
except ImportError:
    psutil = None

# Platform detection for GPU compatibility
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"


# ============================================================================
# BASE POSE PROCESSOR CLASS
# ============================================================================
class PoseProcessor:
    """Base class for pose processing with common functionality"""
    
    def __init__(self, osc_sender, show_fps=False, config=None):
        """
        Initialize pose processor
        
        Args:
            osc_sender: ThreadedOSCSender instance for network communication
            show_fps: Boolean to enable FPS display
            config: Configuration object
        """
        self.osc_sender = osc_sender
        self.show_fps = show_fps
        self.config = config
        self.fps_counter = 0
        self.frame_counter = 0  # For garbage collection even without FPS display
        self.fps_start_time = time.time() if show_fps else None
        self.results = None  # For Tasks async results
        self.pending_frames = 0  # Track frames in MediaPipe's async queue
        self.max_pending_frames = 1  # Maximum frames to queue before skipping (reduced to 1 to prevent buildup)
        self.skipped_frames = 0  # Count of frames skipped due to backpressure
        self._last_detection_state = False  # Track if we had detection last time (for transition to empty)
        self._has_fresh_results = False  # Track if callback delivered new results
        self._display_results = None  # Main-thread-only copy of last taken results for stale drawing

        # Lock protecting results/pending_frames shared with MediaPipe's worker thread
        self._results_lock = threading.Lock()

        # Pre-allocated buffer for resizing to prevent memory fragmentation
        self._resize_buffer = None
        self._rgb_buffer = None

        # Cache per-frame config lookups (config is not mutated after construction)
        camera_config = config.get('camera') if config else {}
        self._proc_width = camera_config.get('processing_width', 640)
        self._proc_height = camera_config.get('processing_height', 480)

        # Maps normalized coords from the (possibly letterboxed) processing
        # frame back to the source frame; identity until the first resize
        self._letterbox_transform = LetterboxTransform(
            1.0, 0, 0, self._proc_width, self._proc_height, self._proc_width, self._proc_height
        )

        if config:
            performance_config = config.get('performance')
            self._gc_enabled = performance_config.get('gc_enabled', True)
            self._gc_interval = performance_config.get('gc_interval', 60)
        else:
            self._gc_enabled = False
            self._gc_interval = 60

        # Pre-build DrawingSpec objects for landmark rendering
        display_config = config.get('display') if config else {}
        landmark_color = tuple(display_config.get('landmark_color', [245, 117, 66]))
        connection_color = tuple(display_config.get('connection_color', [245, 66, 230]))
        self._landmark_spec = mp.solutions.drawing_utils.DrawingSpec(
            color=landmark_color,
            thickness=display_config.get('landmark_thickness', 1),
            circle_radius=display_config.get('landmark_radius', 2)
        )
        self._connection_spec = mp.solutions.drawing_utils.DrawingSpec(
            color=connection_color,
            thickness=display_config.get('connection_thickness', 1),
            circle_radius=display_config.get('connection_radius', 1)
        )
    
    # ------------------------------------------------------------------------
    # OSC data transmission methods
    # ------------------------------------------------------------------------
    
    def send_pose_data(self, pose_landmarks, pose_world_landmarks, timestamp):
        """Send pose data via OSC (single pose)"""
        if pose_landmarks:
            osc_payload = {
                "timestamp": timestamp,
                "landmarks": pose_landmarks
            }
            self.osc_sender.send_message("/pose/raw", compact_json(osc_payload))
        
        if pose_world_landmarks:
            world_payload = {
                "timestamp": timestamp,
                "landmarks": pose_world_landmarks
            }
            self.osc_sender.send_message("/pose/world", compact_json(world_payload))
    
    def send_bounds_data(self, landmarks, world_landmarks, transform=None):
        """Send bounding box data via OSC (single pose)"""
        if landmarks:
            bounds = get_pose_bounds_with_values(landmarks, transform)
            self.osc_sender.send_message("/pose/raw_bounds", compact_json(bounds))

        if world_landmarks:
            # World landmarks are already in real-world metres - no transform
            world_bounds = get_pose_bounds_with_values(world_landmarks)
            self.osc_sender.send_message("/pose/world_bounds", compact_json(world_bounds))
    
    def send_empty_data(self, timestamp):
        """Send empty data to clear stale data on receiving machine"""
        empty_payload = {
            "timestamp": timestamp,
            "landmarks": []
        }
        self.osc_sender.send_message("/pose/raw", compact_json(empty_payload))
        self.osc_sender.send_message("/pose/raw_bounds", compact_json({}))
        self.osc_sender.send_message("/pose/world", compact_json(empty_payload))
        self.osc_sender.send_message("/pose/world_bounds", compact_json({}))
        self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))
    
    def send_multiple_pose_data(self, all_pose_landmarks, all_pose_world_landmarks, timestamp):
        """Send data for multiple detected poses via OSC"""
        if all_pose_landmarks:
            multi_pose_payload = {
                "timestamp": timestamp,
                "poses": all_pose_landmarks,
                "count": len(all_pose_landmarks)
            }
            self.osc_sender.send_message("/pose/multi_raw", compact_json(multi_pose_payload))
            # Individual messages removed to prevent memory leak
        
        if all_pose_world_landmarks:
            multi_world_payload = {
                "timestamp": timestamp,
                "poses": all_pose_world_landmarks,
                "count": len(all_pose_world_landmarks)
            }
            self.osc_sender.send_message("/pose/multi_world", compact_json(multi_world_payload))
            # Individual messages removed to prevent memory leak
    
    def send_multiple_bounds_data(self, all_landmarks, all_world_landmarks, transform=None):
        """Send bounds data for multiple poses via OSC"""
        if all_landmarks:
            all_bounds = []
            for landmarks in all_landmarks:
                bounds = get_pose_bounds_with_values(landmarks, transform)
                all_bounds.append(bounds)
            # Individual messages removed to prevent memory leak
            
            # Send combined bounds data only
            multi_bounds_payload = {
                "poses": all_bounds,
                "count": len(all_bounds)
            }
            self.osc_sender.send_message("/pose/multi_raw_bounds", compact_json(multi_bounds_payload))
            # Clear temporary list
            del all_bounds
        
        if all_world_landmarks:
            all_world_bounds = []
            for world_landmarks in all_world_landmarks:
                world_bounds = get_pose_bounds_with_values(world_landmarks)
                all_world_bounds.append(world_bounds)
            # Individual messages removed to prevent memory leak
            
            # Send combined world bounds data only
            multi_world_bounds_payload = {
                "poses": all_world_bounds,
                "count": len(all_world_bounds)
            }
            self.osc_sender.send_message("/pose/multi_world_bounds", compact_json(multi_world_bounds_payload))
            # Clear temporary list
            del all_world_bounds

    # ------------------------------------------------------------------------
    # Performance monitoring
    # ------------------------------------------------------------------------
    
    def update_fps(self, backend_name):
        """Update and display FPS if enabled (every 30 frames)"""
        self.frame_counter += 1
        
        if self.show_fps:
            self.fps_counter += 1
            if self.fps_counter % 30 == 0:
                fps_end_time = time.time()
                actual_fps = 30 / (fps_end_time - self.fps_start_time)
                # Get memory usage if psutil available
                if psutil is not None:
                    process = psutil.Process()
                    mem_mb = process.memory_info().rss / 1024 / 1024
                    osc_stats = self.osc_sender.get_stats()
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Memory: {mem_mb:.1f}MB | "
                          f"OSC Sent: {osc_stats['sent']} Dropped: {osc_stats['dropped']} Queued: {osc_stats['queued']} | "
                          f"MP Pending: {self.pending_frames} Skipped: {self.skipped_frames}")
                else:
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Skipped: {self.skipped_frames}")
                self.fps_start_time = fps_end_time

        # Force garbage collection at configurable interval (higher = smoother but more memory)
        # Can be disabled entirely via gc_enabled config option
        if self._gc_enabled and self.frame_counter % self._gc_interval == 0:
            gc.collect()

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
        # Convert landmarks for drawing
        pose_landmarks_proto = landmark_pb2.NormalizedLandmarkList()
        pose_landmarks_proto.landmark.extend([
            landmark_pb2.NormalizedLandmark(x=landmark.x, y=landmark.y, z=landmark.z)
            for landmark in landmarks
        ])

        mp.solutions.drawing_utils.draw_landmarks(
            image,
            pose_landmarks_proto,
            mp.solutions.pose.POSE_CONNECTIONS,
            self._landmark_spec,
            self._connection_spec
        )


# ============================================================================
# MEDIAPIPE TASKS PROCESSOR (Modern API with GPU support)
# ============================================================================
class TasksPoseProcessor(PoseProcessor):
    """
    MediaPipe Tasks pose processor
    Supports GPU acceleration and multi-pose detection
    Recommended for new projects
    """
    
    def __init__(self, osc_sender, show_fps=False, config=None, force_cpu=False, force_gpu=False, is_apple_silicon=None):
        """
        Initialize Tasks processor
        
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
        # Use passed value or detect automatically
        self.is_apple_silicon = is_apple_silicon if is_apple_silicon is not None else IS_APPLE_SILICON
        self.use_gpu = False  # Will be set during setup
    
    def setup_processor(self):
        """Setup MediaPipe Tasks processor with GPU/CPU fallback"""
        try:
            # Import MediaPipe Tasks API
            BaseOptions = mp.tasks.BaseOptions
            PoseLandmarker = mp.tasks.vision.PoseLandmarker
            PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
            VisionRunningMode = mp.tasks.vision.RunningMode
            
            # Get MediaPipe configuration
            mp_config = self.config.get('mediapipe') if self.config else {}
            
            # Get pose model type from config
            pose_model_type = mp_config.get('pose_model_type', 'lite')
            
            # Download model if needed
            model_path = download_pose_model(pose_model_type)
            
            if not model_path or not os.path.exists(model_path):
                print("❌ Model file not available")
                return None, None, None, False
            
            # ------------------------------------------------------------------------
            # Determine GPU/CPU delegate strategy
            # ------------------------------------------------------------------------
            if self.force_cpu:
                print("🔧 Forced CPU delegate via command line")
                use_gpu_delegate = False
            elif self.force_gpu:
                print("⚠️  Forced GPU delegate via command line (WARNING: known memory leak on Apple Silicon)")
                use_gpu_delegate = True
            elif self.is_apple_silicon:
                # CRITICAL: MediaPipe GPU delegate has a memory leak on Apple Silicon
                # that causes ~1.2MB per frame accumulation. Force CPU to avoid this.
                print("🍎 Apple Silicon detected: Using CPU delegate (GPU has known memory leak)")
                use_gpu_delegate = False
            else:
                use_gpu_delegate = True
            
            landmarker = None
            backend_name = None
            
            # ------------------------------------------------------------------------
            # Try GPU delegate first (unless forced to CPU or on Apple Silicon)
            # ------------------------------------------------------------------------
            if use_gpu_delegate:
                print("🎯 Attempting GPU delegate...")
                try:
                    delegate = BaseOptions.Delegate.GPU
                    
                    options = PoseLandmarkerOptions(
                        base_options=BaseOptions(
                            model_asset_path=model_path,
                            delegate=delegate
                        ),
                        running_mode=VisionRunningMode.LIVE_STREAM,
                        num_poses=mp_config.get('num_poses', 1),
                        min_pose_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
                        min_pose_presence_confidence=mp_config.get('min_pose_presence_confidence', 0.5),
                        min_tracking_confidence=mp_config.get('min_tracking_confidence', 0.5),
                        result_callback=self._result_callback
                    )
                    
                    landmarker = PoseLandmarker.create_from_options(options)
                    backend_name = "GPU (MediaPipe Tasks)"
                    self.use_gpu = True
                    print("✅ GPU delegate initialized successfully")
                    if self.is_apple_silicon:
                        print("   Using SRGBA image format for Apple Silicon Metal compatibility")
                        
                except Exception as gpu_error:
                    print(f"⚠️  GPU delegate failed during initialization: {gpu_error}")
                    landmarker = None
            
            # ------------------------------------------------------------------------
            # Fallback to CPU delegate if GPU failed or was not attempted
            # ------------------------------------------------------------------------
            if landmarker is None:
                print("🔄 Using CPU delegate...")
                try:
                    delegate = BaseOptions.Delegate.CPU
                    
                    options = PoseLandmarkerOptions(
                        base_options=BaseOptions(
                            model_asset_path=model_path,
                            delegate=delegate
                        ),
                        running_mode=VisionRunningMode.LIVE_STREAM,
                        num_poses=mp_config.get('num_poses', 1),
                        min_pose_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
                        min_pose_presence_confidence=mp_config.get('min_pose_presence_confidence', 0.5),
                        min_tracking_confidence=mp_config.get('min_tracking_confidence', 0.5),
                        result_callback=self._result_callback
                    )
                    
                    landmarker = PoseLandmarker.create_from_options(options)
                    backend_name = "CPU (MediaPipe Tasks)"
                    self.use_gpu = False
                    print("✅ CPU delegate initialized successfully")
                except Exception as cpu_error:
                    print(f"❌ CPU delegate also failed: {cpu_error}")
                    return None, None, None, False
            
            window_title = "MediaPipe Tasks Pose Detection"
            print(f"✅ Successfully initialized {backend_name}")
            return landmarker, backend_name, window_title, True
            
        except ImportError as e:
            print(f"⚠️  MediaPipe Tasks not available: {e}")
            return None, None, None, False
        except Exception as e:
            print(f"❌ Failed to initialize MediaPipe Tasks: {e}")
            return None, None, None, False
    
    def _result_callback(self, result, output_image, timestamp_ms):
        """
        Callback for async pose detection results from MediaPipe Tasks
        Called automatically when processing completes
        Runs on MediaPipe's worker thread - state updates guarded by lock
        Note: We only store the result, not the output_image to avoid memory leaks
        """
        with self._results_lock:
            self.results = result
            self._has_fresh_results = True  # Mark that we have new results to process
            # Decrement pending frame counter
            self.pending_frames = max(0, self.pending_frames - 1)
        # Explicitly don't store output_image - it's not needed and causes memory leaks
        del output_image
    
    def process_frame(self, frame, landmarker, backend_name, timestamp_counter, draw_target=None):
        """
        Process a single frame with MediaPipe Tasks
        Handles frame resizing, color conversion, and Apple Silicon compatibility

        Args:
            frame: Input frame from camera/NDI
            landmarker: MediaPipe PoseLandmarker instance
            backend_name: Backend name for FPS display
            timestamp_counter: Frame counter for async processing
            draw_target: Optional shared display array to draw landmarks into
                instead of the inference frame. Lets a caller composite this
                processor's overlays onto another processor's output (e.g.
                pose + hand in one preview) without feeding annotated pixels
                back into either model. Model input is always the clean
                letterboxed frame, never draw_target. Defaults to None,
                which falls back to today's single-processor behavior:
                draw into (a copy of, if shared) the letterboxed frame.

        Returns:
            Annotated frame with landmarks drawn (draw_target, if provided)
        """
        try:
            if frame is None or frame.size == 0:
                return frame

            # Always resize frame for consistent display, regardless of processing
            proc_width = self._proc_width
            proc_height = self._proc_height

            h, w = frame.shape[:2]
            if w != proc_width or h != proc_height:
                # Letterbox instead of stretching: preserves the source aspect
                # ratio (padding with black bars) so normalized coordinates
                # sent over OSC stay correct relative to the true source frame
                image, letterbox_transform = letterbox_frame(frame, proc_width, proc_height, self._resize_buffer)
                if letterbox_transform.pad_x == 0 and letterbox_transform.pad_y == 0:
                    # No padding needed - image is the reusable resize buffer
                    self._resize_buffer = image
                self._letterbox_transform = letterbox_transform
            else:
                image = frame
                self._letterbox_transform = LetterboxTransform(1.0, 0, 0, proc_width, proc_height, proc_width, proc_height)

            # `image` is the inference input ONLY - always the clean,
            # letterboxed frame, never annotated. `target` is what gets
            # drawn into and returned; a caller-supplied draw_target lets
            # multiple processors composite their overlays onto one shared
            # array in a single loop iteration without leaking one
            # processor's drawings into another's model input.
            target = draw_target if draw_target is not None else (image.copy() if image is self._resize_buffer else image)

            # Check if MediaPipe's async queue is backing up - skip frame if too many pending
            if self.pending_frames >= self.max_pending_frames:
                # Skip MediaPipe processing, but keep the preview skeleton
                # alive by redrawing the last known results - otherwise it
                # blinks on/off every time the model is the bottleneck
                self.skipped_frames += 1
                self.update_fps(backend_name)
                if self._display_results is not None and self._display_results.pose_landmarks:
                    for pose_landmark in self._display_results.pose_landmarks:
                        self._draw_landmarks(target, pose_landmark)
                # No OSC here - a skipped frame means "no new information",
                # not "nothing detected"; sending status would misrepresent one or the other
                return target

            # Convert to RGB for MediaPipe using pre-allocated buffer
            if (self._rgb_buffer is None or 
                self._rgb_buffer.shape[0] != image.shape[0] or 
                self._rgb_buffer.shape[1] != image.shape[1]):
                self._rgb_buffer = np.empty((image.shape[0], image.shape[1], 3), dtype=np.uint8)
            
            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=self._rgb_buffer)
            rgb_frame = self._rgb_buffer
            
            # On Apple Silicon with GPU, use SRGBA format (4 channels) for Metal compatibility
            # The Metal GPU buffer doesn't support SRGB (3 channels), only SRGBA
            if self.is_apple_silicon and self.use_gpu:
                # Convert RGB to RGBA by adding alpha channel
                rgba_frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2RGBA)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGBA, data=rgba_frame)
            else:
                # Standard SRGB format for CPU or non-Apple platforms
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            
            # Process with MediaPipe Tasks (async)
            landmarker.detect_async(mp_image, timestamp_counter)
            with self._results_lock:
                self.pending_frames += 1

            # Explicitly clear reference to mp_image - data was already copied
            del mp_image
            
            timestamp = time.time()

            # Atomically check-and-take fresh results from the callback thread
            # (hold the lock only for the swap - never during serialization/sends/drawing)
            fresh_results = None
            with self._results_lock:
                if self._has_fresh_results and self.results is not None:
                    fresh_results = self.results
                    self.results = None
                    self._has_fresh_results = False  # Reset flag

            # Only process and send OSC when we have fresh results from the callback
            # This ensures OSC messages are synchronized with actual detection rate
            if fresh_results is not None:
                # Keep for stale drawing on frames before the next callback lands (main thread only)
                self._display_results = fresh_results
                pose_detected = bool(fresh_results.pose_landmarks)
                # Snapshot for this call - stable for the duration of process_frame
                transform = self._letterbox_transform

                if pose_detected and len(fresh_results.pose_landmarks) > 0:
                    self._last_detection_state = True
                    # Process all detected poses
                    all_pose_landmarks = []
                    all_pose_world_landmarks = []

                    # Process each detected pose
                    for i, pose_landmark in enumerate(fresh_results.pose_landmarks):
                        pose_landmarks = process_landmarks_to_dict(pose_landmark, f"pose_{i}", transform)
                        all_pose_landmarks.append(pose_landmarks)

                    # Process world landmarks if available (already real-world metres - no transform)
                    if (hasattr(fresh_results, 'pose_world_landmarks') and
                        fresh_results.pose_world_landmarks):
                        for i, pose_world_landmark in enumerate(fresh_results.pose_world_landmarks):
                            pose_world_landmarks = process_landmarks_to_dict(pose_world_landmark, f"pose_world_{i}")
                            all_pose_world_landmarks.append(pose_world_landmarks)

                    # Send data for each pose individually
                    for i in range(len(all_pose_landmarks)):
                        pose_landmarks = all_pose_landmarks[i]
                        pose_world_landmarks = all_pose_world_landmarks[i] if i < len(all_pose_world_landmarks) else None
                        self.send_pose_data(pose_landmarks, pose_world_landmarks, timestamp)

                        # Send bounds for this pose
                        self.send_bounds_data(
                            fresh_results.pose_landmarks[i],
                            fresh_results.pose_world_landmarks[i] if pose_world_landmarks else None,
                            transform
                        )

                    self.osc_sender.send_message("/mp/status", compact_json({"status": len(fresh_results.pose_landmarks)}))

                    # Draw all pose landmarks
                    for pose_landmark in fresh_results.pose_landmarks:
                        self._draw_landmarks(target, pose_landmark)

                    # Clear temporary lists to free memory
                    del all_pose_landmarks
                    del all_pose_world_landmarks
                else:
                    # Always send status message so receivers know program is running
                    self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))
                    # Only send empty data once when transitioning from detected to not detected
                    if self._last_detection_state:
                        self.send_empty_data(timestamp)
                        self._last_detection_state = False
            elif self._display_results is not None:
                # We have results but they're stale (already processed), just draw landmarks
                if self._display_results.pose_landmarks:
                    for pose_landmark in self._display_results.pose_landmarks:
                        self._draw_landmarks(target, pose_landmark)
                # No fresh detection this frame - status 0 signals no actively tracked person
                self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))
            else:
                # No results yet - still send status so receivers know program is running
                self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))

            # Clear intermediate frames to free memory
            del rgb_frame
            if 'rgba_frame' in locals():
                del rgba_frame

            self.update_fps(backend_name)
            return target

        except Exception as e:
            print(f"⚠️  Tasks frame processing error: {e}")
            # Clear results on error to prevent memory leak (under lock - shared with callback thread)
            with self._results_lock:
                self.results = None
                self._has_fresh_results = False
            self._display_results = None
            return draw_target if draw_target is not None else frame


# ============================================================================
# LEGACY MEDIAPIPE PROCESSOR (Older API, CPU only, single pose)
# ============================================================================
class LegacyPoseProcessor(PoseProcessor):
    """
    Legacy MediaPipe pose processor
    Uses older API, CPU only, single pose detection
    Fallback when Tasks API is not available
    """
    
    def setup_processor(self):
        """
        Setup Legacy MediaPipe processor
        Only supports single pose detection
        
        Returns:
            Tuple of (pose_context, backend_name, window_title)
        """
        # Get MediaPipe configuration
        mp_config = self.config.get('mediapipe') if self.config else {}
        
        # Warn if num_poses > 1 since Legacy mode only supports 1 pose
        if mp_config.get('num_poses', 1) > 1:
            print("⚠️  Legacy mode only supports single pose detection. num_poses setting will be ignored.")
        
        backend_name = "Legacy MediaPipe"
        window_title = "Legacy Pose Detection"
        print("✅ Using Legacy MediaPipe")
        
        pose_context = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=mp_config.get('model_complexity', 0),
            smooth_landmarks=mp_config.get('smooth_landmarks', True),
            enable_segmentation=mp_config.get('enable_segmentation', False),
            smooth_segmentation=False,
            min_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
            min_tracking_confidence=mp_config.get('min_tracking_confidence', 0.5)
        )
        
        return pose_context, backend_name, window_title
    
    def process_frame(self, frame, pose_context, backend_name, draw_target=None):
        """
        Process a single frame with Legacy MediaPipe
        Simpler processing for single pose only

        Args:
            frame: Input frame from camera/NDI
            pose_context: MediaPipe Pose context manager
            backend_name: Backend name for FPS display
            draw_target: Optional shared display array to draw landmarks
                into instead of the inference frame - see
                TasksPoseProcessor.process_frame for the full rationale.
                Defaults to None, which falls back to today's
                single-processor behavior.

        Returns:
            Annotated frame with landmarks drawn (draw_target, if provided)
        """
        try:
            # Resize frame for processing if needed
            proc_width = self._proc_width
            proc_height = self._proc_height

            h, w = frame.shape[:2]
            if w != proc_width or h != proc_height:
                # Letterbox instead of stretching: preserves the source aspect
                # ratio (padding with black bars) so normalized coordinates
                # sent over OSC stay correct relative to the true source frame
                image, letterbox_transform = letterbox_frame(frame, proc_width, proc_height, self._resize_buffer)
                if letterbox_transform.pad_x == 0 and letterbox_transform.pad_y == 0:
                    # No padding needed - image is the reusable resize buffer
                    self._resize_buffer = image
                self._letterbox_transform = letterbox_transform
            else:
                # Use frame directly, avoid copy
                image = frame
                self._letterbox_transform = LetterboxTransform(1.0, 0, 0, proc_width, proc_height, proc_width, proc_height)

            # `image` is the inference input ONLY - always the clean,
            # letterboxed frame, never annotated. `target` is what gets
            # drawn into and returned - see TasksPoseProcessor.process_frame
            # for the full rationale.
            target = draw_target if draw_target is not None else (image.copy() if image is self._resize_buffer else image)

            # Convert to RGB for MediaPipe using pre-allocated buffer
            if (self._rgb_buffer is None or
                self._rgb_buffer.shape[0] != image.shape[0] or
                self._rgb_buffer.shape[1] != image.shape[1]):
                self._rgb_buffer = np.empty((image.shape[0], image.shape[1], 3), dtype=np.uint8)

            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=self._rgb_buffer)
            rgb_image = self._rgb_buffer

            # Process with MediaPipe Pose
            results = pose_context.process(rgb_image)

            timestamp = time.time()
            
            pose_detected = bool(results.pose_landmarks)
            
            if pose_detected:
                self._last_detection_state = True
                # Process landmarks
                pose_landmarks = process_landmarks_to_dict(
                    results.pose_landmarks.landmark, "pose", self._letterbox_transform
                )

                # World landmarks are already real-world metres - no transform
                pose_world_landmarks = []
                if results.pose_world_landmarks:
                    pose_world_landmarks = process_landmarks_to_dict(
                        results.pose_world_landmarks.landmark, "pose_world"
                    )

                # Send data
                self.send_pose_data(pose_landmarks, pose_world_landmarks, timestamp)
                self.send_bounds_data(
                    results.pose_landmarks.landmark,
                    results.pose_world_landmarks.landmark if pose_world_landmarks else None,
                    self._letterbox_transform
                )
                
                self.osc_sender.send_message("/mp/status", compact_json({"status": 1}))
                
                # Draw pose landmarks
                if results.pose_landmarks:
                    self._draw_landmarks(target, results.pose_landmarks)

                # Clear temporary lists to free memory
                del pose_landmarks
                del pose_world_landmarks
            else:
                # Always send status message so receivers know program is running
                self.osc_sender.send_message("/mp/status", compact_json({"status": 0}))
                # Only send empty data once when transitioning from detected to not detected
                if self._last_detection_state:
                    self.send_empty_data(timestamp)
                    self._last_detection_state = False

            self.update_fps(backend_name)
            return target

        except Exception as e:
            print(f"⚠️  Legacy frame processing error: {e}")
            # Ensure we don't hold references on error
            if 'image' in locals() and image is not frame:
                del image
            return draw_target if draw_target is not None else frame
    
    def _draw_landmarks(self, image, pose_landmarks):
        """
        Draw pose landmarks on image
        Overrides the base version: the legacy API already hands back a
        NormalizedLandmarkList proto, so no conversion is needed

        Args:
            image: Image to draw on
            pose_landmarks: MediaPipe pose landmarks object
        """
        mp.solutions.drawing_utils.draw_landmarks(
            image,
            pose_landmarks,
            mp.solutions.pose.POSE_CONNECTIONS,
            self._landmark_spec,
            self._connection_spec
        )


# ============================================================================
# BACKWARD COMPATIBILITY ALIASES
# ============================================================================
# Legacy aliases for older code
GPUPoseProcessor = TasksPoseProcessor
CPUPoseProcessor = LegacyPoseProcessor
