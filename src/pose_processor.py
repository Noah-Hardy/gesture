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
import json
import platform
import gc
import sys
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.framework.formats import landmark_pb2

from .pose_utils import get_pose_bounds_with_values, process_landmarks_to_dict
from .model_downloader import download_pose_model

# Platform detection for GPU compatibility
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def compact_json(data):
    """
    Create compact JSON string to minimize memory usage
    Creates new string each time to avoid interning issues
    """
    # Use separators to minimize whitespace
    json_str = json.dumps(data, separators=(',', ':'))
    # Return as bytes to avoid string interning in Python
    return json_str


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
        
        # Pre-allocated buffer for resizing to prevent memory fragmentation
        self._resize_buffer = None
        self._rgb_buffer = None
    
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
    
    def send_bounds_data(self, landmarks, world_landmarks):
        """Send bounding box data via OSC (single pose)"""
        if landmarks:
            bounds = get_pose_bounds_with_values(landmarks)
            self.osc_sender.send_message("/pose/raw_bounds", compact_json(bounds))
        
        if world_landmarks:
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
    
    def send_multiple_bounds_data(self, all_landmarks, all_world_landmarks):
        """Send bounds data for multiple poses via OSC"""
        if all_landmarks:
            all_bounds = []
            for landmarks in all_landmarks:
                bounds = get_pose_bounds_with_values(landmarks)
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
                try:
                    import psutil
                    process = psutil.Process()
                    mem_mb = process.memory_info().rss / 1024 / 1024
                    osc_stats = self.osc_sender.get_stats()
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Memory: {mem_mb:.1f}MB | "
                          f"OSC Sent: {osc_stats['sent']} Dropped: {osc_stats['dropped']} Queued: {osc_stats['queued']} | "
                          f"MP Pending: {self.pending_frames} Skipped: {self.skipped_frames}")
                except ImportError:
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Skipped: {self.skipped_frames}")
                self.fps_start_time = fps_end_time
        
        # Force garbage collection every 30 frames to prevent memory buildup
        if self.frame_counter % 30 == 0:
            gc.collect()


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
        Note: We only store the result, not the output_image to avoid memory leaks
        """
        self.results = result
        # Decrement pending frame counter
        self.pending_frames = max(0, self.pending_frames - 1)
        # Explicitly don't store output_image - it's not needed and causes memory leaks
        del output_image
    
    def process_frame(self, frame, landmarker, backend_name, timestamp_counter):
        """
        Process a single frame with MediaPipe Tasks
        Handles frame resizing, color conversion, and Apple Silicon compatibility
        
        Args:
            frame: Input frame from camera/NDI
            landmarker: MediaPipe PoseLandmarker instance
            backend_name: Backend name for FPS display
            timestamp_counter: Frame counter for async processing
            
        Returns:
            Annotated frame with landmarks drawn
        """
        try:
            if frame is None or frame.size == 0:
                return frame
            
            # Always resize frame for consistent display, regardless of processing
            camera_config = self.config.get('camera') if self.config else {}
            proc_width = camera_config.get('processing_width', 640)
            proc_height = camera_config.get('processing_height', 480)
            
            h, w = frame.shape[:2]
            if w != proc_width or h != proc_height:
                # Use pre-allocated buffer if available and correct size
                if (self._resize_buffer is None or 
                    self._resize_buffer.shape[0] != proc_height or 
                    self._resize_buffer.shape[1] != proc_width):
                    self._resize_buffer = np.empty((proc_height, proc_width, 3), dtype=np.uint8)
                
                # Resize into pre-allocated buffer
                cv2.resize(frame, (proc_width, proc_height), dst=self._resize_buffer, interpolation=cv2.INTER_LINEAR)
                image = self._resize_buffer
            else:
                image = frame
            
            # Check if MediaPipe's async queue is backing up - skip frame if too many pending
            if self.pending_frames >= self.max_pending_frames:
                # Skip MediaPipe processing but return properly resized frame for display
                self.skipped_frames += 1
                self.update_fps(backend_name)
                # Return a copy for display since we reuse the buffer
                return image.copy() if image is self._resize_buffer else image
            
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
            self.pending_frames += 1
            
            # Explicitly clear reference to mp_image - data was already copied
            del mp_image
            
            timestamp = time.time()
            
            # Convert RGB back to BGR for OpenCV display - reuse resize buffer if available
            if self._resize_buffer is not None and self._resize_buffer.shape == rgb_frame.shape:
                cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR, dst=self._resize_buffer)
                image = self._resize_buffer
            else:
                image = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
            
            # Process results if available
            if self.results is not None:
                pose_detected = bool(self.results.pose_landmarks)
                
                if pose_detected and len(self.results.pose_landmarks) > 0:
                    # Process all detected poses
                    all_pose_landmarks = []
                    all_pose_world_landmarks = []
                    
                    # Process each detected pose
                    for i, pose_landmark in enumerate(self.results.pose_landmarks):
                        pose_landmarks = process_landmarks_to_dict(pose_landmark, f"pose_{i}")
                        all_pose_landmarks.append(pose_landmarks)
                    
                    # Process world landmarks if available
                    if (hasattr(self.results, 'pose_world_landmarks') and 
                        self.results.pose_world_landmarks):
                        for i, pose_world_landmark in enumerate(self.results.pose_world_landmarks):
                            pose_world_landmarks = process_landmarks_to_dict(pose_world_landmark, f"pose_world_{i}")
                            all_pose_world_landmarks.append(pose_world_landmarks)
                    
                    # Send data for each pose individually
                    for i in range(len(all_pose_landmarks)):
                        pose_landmarks = all_pose_landmarks[i]
                        pose_world_landmarks = all_pose_world_landmarks[i] if i < len(all_pose_world_landmarks) else None
                        self.send_pose_data(pose_landmarks, pose_world_landmarks, timestamp)
                        
                        # Send bounds for this pose
                        self.send_bounds_data(
                            self.results.pose_landmarks[i],
                            self.results.pose_world_landmarks[i] if pose_world_landmarks else None
                        )
                    
                    self.osc_sender.send_message("/mp/status", compact_json({"status": len(self.results.pose_landmarks)}))
                    
                    # Draw all pose landmarks
                    for pose_landmark in self.results.pose_landmarks:
                        self._draw_landmarks(image, pose_landmark)
                    
                    # Clear temporary lists to free memory
                    del all_pose_landmarks
                    del all_pose_world_landmarks
                else:
                    self.send_empty_data(timestamp)
                
                # Clear results after processing to prevent accumulation
                self.results = None
            else:
                # No results yet
                self.send_empty_data(timestamp)
            
            # Clear intermediate frames to free memory
            del rgb_frame
            if 'rgba_frame' in locals():
                del rgba_frame
            
            self.update_fps(backend_name)
            return image
            
        except Exception as e:
            print(f"⚠️  Tasks frame processing error: {e}")
            # Clear results on error to prevent memory leak
            self.results = None
            return frame
    
    def _draw_landmarks(self, image, landmarks):
        """
        Draw pose landmarks on image
        Uses configuration for colors and styling
        
        Args:
            image: Image to draw on
            landmarks: Landmark list to draw
        """
        # Get display configuration
        display_config = self.config.get('display') if self.config else {}
        landmark_color = tuple(display_config.get('landmark_color', [245, 117, 66]))
        connection_color = tuple(display_config.get('connection_color', [245, 66, 230]))
        landmark_thickness = display_config.get('landmark_thickness', 1)
        landmark_radius = display_config.get('landmark_radius', 2)
        connection_thickness = display_config.get('connection_thickness', 1)
        connection_radius = display_config.get('connection_radius', 1)
        
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
            mp.solutions.drawing_utils.DrawingSpec(
                color=landmark_color, 
                thickness=landmark_thickness, 
                circle_radius=landmark_radius
            ),
            mp.solutions.drawing_utils.DrawingSpec(
                color=connection_color, 
                thickness=connection_thickness, 
                circle_radius=connection_radius
            )
        )


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
    
    def process_frame(self, frame, pose_context, backend_name):
        """
        Process a single frame with Legacy MediaPipe
        Simpler processing for single pose only
        
        Args:
            frame: Input frame from camera/NDI
            pose_context: MediaPipe Pose context manager
            backend_name: Backend name for FPS display
            
        Returns:
            Annotated frame with landmarks drawn
        """
        try:
            # Resize frame for processing if needed
            camera_config = self.config.get('camera') if self.config else {}
            proc_width = camera_config.get('processing_width', 640)
            proc_height = camera_config.get('processing_height', 480)
            
            h, w = frame.shape[:2]
            if w != proc_width or h != proc_height:
                # Use pre-allocated buffer if available and correct size
                if (self._resize_buffer is None or 
                    self._resize_buffer.shape[0] != proc_height or 
                    self._resize_buffer.shape[1] != proc_width):
                    self._resize_buffer = np.empty((proc_height, proc_width, 3), dtype=np.uint8)
                
                cv2.resize(frame, (proc_width, proc_height), dst=self._resize_buffer, interpolation=cv2.INTER_LINEAR)
                image = self._resize_buffer
            else:
                # Use frame directly, avoid copy
                image = frame
            
            # Convert to RGB for MediaPipe using pre-allocated buffer
            if (self._rgb_buffer is None or 
                self._rgb_buffer.shape[0] != image.shape[0] or 
                self._rgb_buffer.shape[1] != image.shape[1]):
                self._rgb_buffer = np.empty((image.shape[0], image.shape[1], 3), dtype=np.uint8)
            
            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=self._rgb_buffer)
            rgb_image = self._rgb_buffer
            
            # Process with MediaPipe Pose
            results = pose_context.process(rgb_image)
            
            # Convert back to BGR for display - reuse resize buffer
            if self._resize_buffer is not None and self._resize_buffer.shape == rgb_image.shape:
                cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR, dst=self._resize_buffer)
                image = self._resize_buffer
            else:
                image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)
            timestamp = time.time()
            
            pose_detected = bool(results.pose_landmarks)
            
            if pose_detected:
                # Process landmarks
                pose_landmarks = process_landmarks_to_dict(
                    results.pose_landmarks.landmark, "pose"
                )
                
                pose_world_landmarks = []
                if results.pose_world_landmarks:
                    pose_world_landmarks = process_landmarks_to_dict(
                        results.pose_world_landmarks.landmark, "pose_world"
                    )
                
                # Send data
                self.send_pose_data(pose_landmarks, pose_world_landmarks, timestamp)
                self.send_bounds_data(
                    results.pose_landmarks.landmark,
                    results.pose_world_landmarks.landmark if pose_world_landmarks else None
                )
                
                self.osc_sender.send_message("/mp/status", compact_json({"status": 1}))
                
                # Draw pose landmarks
                if results.pose_landmarks:
                    self._draw_landmarks(image, results.pose_landmarks)
                
                # Clear temporary lists to free memory
                del pose_landmarks
                del pose_world_landmarks
            else:
                self.send_empty_data(timestamp)
            
            self.update_fps(backend_name)
            return image
            
        except Exception as e:
            print(f"⚠️  Legacy frame processing error: {e}")
            # Ensure we don't hold references on error
            if 'image' in locals() and image is not frame:
                del image
            return frame
    
    def _draw_landmarks(self, image, pose_landmarks):
        """
        Draw pose landmarks on image
        Uses configuration for colors and styling
        
        Args:
            image: Image to draw on
            pose_landmarks: MediaPipe pose landmarks object
        """
        # Get display configuration
        display_config = self.config.get('display') if self.config else {}
        landmark_color = tuple(display_config.get('landmark_color', [245, 117, 66]))
        connection_color = tuple(display_config.get('connection_color', [245, 66, 230]))
        landmark_thickness = display_config.get('landmark_thickness', 1)
        landmark_radius = display_config.get('landmark_radius', 2)
        connection_thickness = display_config.get('connection_thickness', 1)
        connection_radius = display_config.get('connection_radius', 1)
        
        mp.solutions.drawing_utils.draw_landmarks(
            image,
            pose_landmarks,
            mp.solutions.pose.POSE_CONNECTIONS,
            mp.solutions.drawing_utils.DrawingSpec(
                color=landmark_color, 
                thickness=landmark_thickness, 
                circle_radius=landmark_radius
            ),
            mp.solutions.drawing_utils.DrawingSpec(
                color=connection_color, 
                thickness=connection_thickness, 
                circle_radius=connection_radius
            )
        )


# ============================================================================
# BACKWARD COMPATIBILITY ALIASES
# ============================================================================
# Legacy aliases for older code
GPUPoseProcessor = TasksPoseProcessor
CPUPoseProcessor = LegacyPoseProcessor
