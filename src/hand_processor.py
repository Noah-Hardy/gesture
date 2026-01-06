#!/usr/bin/env python3
"""
Hand Processing Module
Implements MediaPipe Tasks hand landmarker detection
Supports GPU acceleration and multi-hand tracking
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import time
import json
import platform
import gc
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.framework.formats import landmark_pb2

from .pose_utils import get_pose_bounds_with_values, process_landmarks_to_dict
from .model_downloader import download_hand_model

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
    json_str = json.dumps(data, separators=(',', ':'))
    return json_str


# ============================================================================
# HAND LANDMARK CONNECTIONS
# ============================================================================
# MediaPipe hand connections for drawing
HAND_CONNECTIONS = mp.solutions.hands.HAND_CONNECTIONS


# ============================================================================
# HAND PROCESSOR CLASS
# ============================================================================
class HandProcessor:
    """Base class for hand processing with common functionality"""
    
    def __init__(self, osc_sender, show_fps=False, config=None):
        """
        Initialize hand processor
        
        Args:
            osc_sender: ThreadedOSCSender instance for network communication
            show_fps: Boolean to enable FPS display
            config: Configuration object
        """
        self.osc_sender = osc_sender
        self.show_fps = show_fps
        self.config = config
        self.fps_counter = 0
        self.frame_counter = 0
        self.fps_start_time = time.time() if show_fps else None
        self.results = None
        self.pending_frames = 0
        self.max_pending_frames = 1
        self.skipped_frames = 0
        
        # Pre-allocated buffer for resizing to prevent memory fragmentation
        self._resize_buffer = None
        self._rgb_buffer = None
    
    # ------------------------------------------------------------------------
    # OSC data transmission methods
    # ------------------------------------------------------------------------
    
    def send_hand_data(self, hand_landmarks, handedness, hand_idx, timestamp):
        """Send hand data via OSC (single hand)"""
        if hand_landmarks:
            osc_payload = {
                "timestamp": timestamp,
                "hand_index": hand_idx,
                "handedness": handedness,
                "landmarks": hand_landmarks
            }
            self.osc_sender.send_message(f"/hand/{hand_idx}/raw", compact_json(osc_payload))
    
    def send_hand_bounds_data(self, landmarks, hand_idx):
        """Send bounding box data via OSC (single hand)"""
        if landmarks:
            bounds = get_pose_bounds_with_values(landmarks)
            self.osc_sender.send_message(f"/hand/{hand_idx}/bounds", compact_json(bounds))
    
    def send_empty_hand_data(self, timestamp):
        """Send empty data to clear stale data on receiving machine"""
        empty_payload = {
            "timestamp": timestamp,
            "landmarks": []
        }
        self.osc_sender.send_message("/hand/raw", compact_json(empty_payload))
        self.osc_sender.send_message("/hand/bounds", compact_json({}))
        self.osc_sender.send_message("/hand/status", compact_json({"status": 0}))
    
    def send_multiple_hand_data(self, all_hand_landmarks, all_handedness, timestamp):
        """Send data for multiple detected hands via OSC"""
        if all_hand_landmarks:
            multi_hand_payload = {
                "timestamp": timestamp,
                "hands": all_hand_landmarks,
                "handedness": all_handedness,
                "count": len(all_hand_landmarks)
            }
            self.osc_sender.send_message("/hand/multi_raw", compact_json(multi_hand_payload))
    
    def send_multiple_hand_bounds_data(self, all_landmarks):
        """Send bounds data for multiple hands via OSC"""
        if all_landmarks:
            all_bounds = []
            for landmarks in all_landmarks:
                bounds = get_pose_bounds_with_values(landmarks)
                all_bounds.append(bounds)
            
            multi_bounds_payload = {
                "hands": all_bounds,
                "count": len(all_bounds)
            }
            self.osc_sender.send_message("/hand/multi_bounds", compact_json(multi_bounds_payload))
            del all_bounds

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
                try:
                    import psutil
                    process = psutil.Process()
                    mem_mb = process.memory_info().rss / 1024 / 1024
                    osc_stats = self.osc_sender.get_stats()
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Memory: {mem_mb:.1f}MB | "
                          f"OSC Sent: {osc_stats['sent']} Dropped: {osc_stats['dropped']} Queued: {osc_stats['queued']} | "
                          f"Pending: {self.pending_frames} Skipped: {self.skipped_frames}")
                except ImportError:
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Skipped: {self.skipped_frames}")
                self.fps_start_time = fps_end_time
        
        # Force garbage collection every 30 frames to prevent memory buildup
        if self.frame_counter % 30 == 0:
            gc.collect()


# ============================================================================
# MEDIAPIPE TASKS HAND PROCESSOR
# ============================================================================
class TasksHandProcessor(HandProcessor):
    """
    MediaPipe Tasks hand processor
    Supports GPU acceleration and multi-hand detection
    """
    
    def __init__(self, osc_sender, show_fps=False, config=None, force_cpu=False, force_gpu=False, is_apple_silicon=None):
        """
        Initialize Tasks hand processor
        
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
        self.is_apple_silicon = is_apple_silicon if is_apple_silicon is not None else IS_APPLE_SILICON
        self.use_gpu = False
    
    def setup_processor(self):
        """Setup MediaPipe Tasks hand processor with GPU/CPU fallback"""
        try:
            # Import MediaPipe Tasks API
            BaseOptions = mp.tasks.BaseOptions
            HandLandmarker = mp.tasks.vision.HandLandmarker
            HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
            VisionRunningMode = mp.tasks.vision.RunningMode
            
            # Download model if needed
            model_path = download_hand_model()
            
            if not model_path or not os.path.exists(model_path):
                print("❌ Hand model file not available")
                return None, None, None, False
            
            # Get hand configuration
            hand_config = self.config.get('hand') if self.config else {}
            
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
            
            landmarker = None
            backend_name = None
            
            # Try GPU delegate first (unless forced to CPU or on Apple Silicon)
            if use_gpu_delegate:
                print("🎯 Attempting GPU delegate for hand tracking...")
                try:
                    delegate = BaseOptions.Delegate.GPU
                    
                    options = HandLandmarkerOptions(
                        base_options=BaseOptions(
                            model_asset_path=model_path,
                            delegate=delegate
                        ),
                        running_mode=VisionRunningMode.LIVE_STREAM,
                        num_hands=hand_config.get('num_hands', 2),
                        min_hand_detection_confidence=hand_config.get('min_detection_confidence', 0.5),
                        min_hand_presence_confidence=hand_config.get('min_presence_confidence', 0.5),
                        min_tracking_confidence=hand_config.get('min_tracking_confidence', 0.5),
                        result_callback=self._result_callback
                    )
                    
                    landmarker = HandLandmarker.create_from_options(options)
                    backend_name = "GPU (MediaPipe Tasks - Hand)"
                    self.use_gpu = True
                    print("✅ GPU delegate initialized successfully for hand tracking")
                    if self.is_apple_silicon:
                        print("   Using SRGBA image format for Apple Silicon Metal compatibility")
                        
                except Exception as gpu_error:
                    print(f"⚠️  GPU delegate failed during initialization: {gpu_error}")
                    landmarker = None
            
            # Fallback to CPU delegate if GPU failed or was not attempted
            if landmarker is None:
                print("🔄 Using CPU delegate for hand tracking...")
                try:
                    delegate = BaseOptions.Delegate.CPU
                    
                    options = HandLandmarkerOptions(
                        base_options=BaseOptions(
                            model_asset_path=model_path,
                            delegate=delegate
                        ),
                        running_mode=VisionRunningMode.LIVE_STREAM,
                        num_hands=hand_config.get('num_hands', 2),
                        min_hand_detection_confidence=hand_config.get('min_detection_confidence', 0.5),
                        min_hand_presence_confidence=hand_config.get('min_presence_confidence', 0.5),
                        min_tracking_confidence=hand_config.get('min_tracking_confidence', 0.5),
                        result_callback=self._result_callback
                    )
                    
                    landmarker = HandLandmarker.create_from_options(options)
                    backend_name = "CPU (MediaPipe Tasks - Hand)"
                    self.use_gpu = False
                    print("✅ CPU delegate initialized successfully for hand tracking")
                except Exception as cpu_error:
                    print(f"❌ CPU delegate also failed: {cpu_error}")
                    return None, None, None, False
            
            window_title = "MediaPipe Tasks Hand Detection"
            print(f"✅ Successfully initialized {backend_name}")
            return landmarker, backend_name, window_title, True
            
        except ImportError as e:
            print(f"⚠️  MediaPipe Tasks not available: {e}")
            return None, None, None, False
        except Exception as e:
            print(f"❌ Failed to initialize MediaPipe Tasks for hand: {e}")
            return None, None, None, False
    
    def _result_callback(self, result, output_image, timestamp_ms):
        """
        Callback for async hand detection results from MediaPipe Tasks
        """
        self.results = result
        self.pending_frames = max(0, self.pending_frames - 1)
        del output_image
    
    def process_frame(self, frame, landmarker, backend_name, timestamp_counter):
        """
        Process a single frame with MediaPipe Tasks hand landmarker
        
        Args:
            frame: Input frame from camera/NDI
            landmarker: MediaPipe HandLandmarker instance
            backend_name: Backend name for FPS display
            timestamp_counter: Frame counter for async processing
            
        Returns:
            Annotated frame with landmarks drawn
        """
        try:
            if frame is None or frame.size == 0:
                return frame
            
            # Always resize frame for consistent display
            camera_config = self.config.get('camera') if self.config else {}
            proc_width = camera_config.get('processing_width', 640)
            proc_height = camera_config.get('processing_height', 480)
            
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
            
            # Check if MediaPipe's async queue is backing up
            if self.pending_frames >= self.max_pending_frames:
                self.skipped_frames += 1
                self.update_fps(backend_name)
                return image.copy() if image is self._resize_buffer else image
            
            # Convert to RGB for MediaPipe
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
            self.pending_frames += 1
            
            del mp_image
            
            timestamp = time.time()
            
            # Convert RGB back to BGR for OpenCV display
            if self._resize_buffer is not None and self._resize_buffer.shape == rgb_frame.shape:
                cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR, dst=self._resize_buffer)
                image = self._resize_buffer
            else:
                image = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
            
            # Process results if available
            if self.results is not None:
                hands_detected = bool(self.results.hand_landmarks)
                
                if hands_detected and len(self.results.hand_landmarks) > 0:
                    all_hand_landmarks = []
                    all_handedness = []
                    
                    # Process each detected hand
                    for i, hand_landmark in enumerate(self.results.hand_landmarks):
                        hand_landmarks = process_landmarks_to_dict(hand_landmark, f"hand_{i}")
                        all_hand_landmarks.append(hand_landmarks)
                        
                        # Get handedness (left/right)
                        if self.results.handedness and i < len(self.results.handedness):
                            handedness = self.results.handedness[i][0].category_name
                        else:
                            handedness = "Unknown"
                        all_handedness.append(handedness)
                        
                        # Send individual hand data
                        self.send_hand_data(hand_landmarks, handedness, i, timestamp)
                        self.send_hand_bounds_data(hand_landmark, i)
                    
                    # Send combined data for all hands
                    self.send_multiple_hand_data(all_hand_landmarks, all_handedness, timestamp)
                    self.send_multiple_hand_bounds_data(self.results.hand_landmarks)
                    
                    self.osc_sender.send_message("/hand/status", compact_json({"status": len(self.results.hand_landmarks)}))
                    
                    # Draw all hand landmarks
                    for i, hand_landmark in enumerate(self.results.hand_landmarks):
                        handedness = all_handedness[i] if i < len(all_handedness) else "Unknown"
                        self._draw_landmarks(image, hand_landmark, handedness)
                    
                    del all_hand_landmarks
                    del all_handedness
                else:
                    self.send_empty_hand_data(timestamp)
                
                self.results = None
            else:
                self.send_empty_hand_data(timestamp)
            
            del rgb_frame
            if 'rgba_frame' in locals():
                del rgba_frame
            
            self.update_fps(backend_name)
            return image
            
        except Exception as e:
            print(f"⚠️  Hand frame processing error: {e}")
            self.results = None
            return frame
    
    def _draw_landmarks(self, image, landmarks, handedness="Unknown"):
        """
        Draw hand landmarks on image
        Uses configuration for colors and styling
        Different colors for left and right hands
        
        Args:
            image: Image to draw on
            landmarks: Landmark list to draw
            handedness: "Left" or "Right" hand indicator
        """
        # Get display configuration
        display_config = self.config.get('display') if self.config else {}
        hand_config = self.config.get('hand') if self.config else {}
        
        # Use different colors for left and right hands
        if handedness == "Left":
            landmark_color = tuple(hand_config.get('left_landmark_color', [0, 255, 0]))  # Green
            connection_color = tuple(hand_config.get('left_connection_color', [0, 200, 0]))
        else:
            landmark_color = tuple(hand_config.get('right_landmark_color', [255, 0, 0]))  # Red/Blue
            connection_color = tuple(hand_config.get('right_connection_color', [200, 0, 0]))
        
        landmark_thickness = display_config.get('landmark_thickness', 1)
        landmark_radius = display_config.get('landmark_radius', 2)
        connection_thickness = display_config.get('connection_thickness', 1)
        connection_radius = display_config.get('connection_radius', 1)
        
        # Convert landmarks for drawing
        hand_landmarks_proto = landmark_pb2.NormalizedLandmarkList()
        hand_landmarks_proto.landmark.extend([
            landmark_pb2.NormalizedLandmark(x=landmark.x, y=landmark.y, z=landmark.z) 
            for landmark in landmarks
        ])
        
        mp.solutions.drawing_utils.draw_landmarks(
            image,
            hand_landmarks_proto,
            HAND_CONNECTIONS,
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
# LEGACY HAND PROCESSOR (Using older solutions API)
# ============================================================================
class LegacyHandProcessor(HandProcessor):
    """
    Legacy MediaPipe hand processor
    Uses older API, CPU only
    Fallback when Tasks API is not available
    """
    
    def setup_processor(self):
        """
        Setup Legacy MediaPipe hand processor
        
        Returns:
            Tuple of (hand_context, backend_name, window_title)
        """
        hand_config = self.config.get('hand') if self.config else {}
        
        backend_name = "Legacy MediaPipe Hand"
        window_title = "Legacy Hand Detection"
        print("✅ Using Legacy MediaPipe Hand")
        
        hand_context = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=hand_config.get('num_hands', 2),
            model_complexity=hand_config.get('model_complexity', 0),
            min_detection_confidence=hand_config.get('min_detection_confidence', 0.5),
            min_tracking_confidence=hand_config.get('min_tracking_confidence', 0.5)
        )
        
        return hand_context, backend_name, window_title
    
    def process_frame(self, frame, hand_context, backend_name):
        """
        Process a single frame with Legacy MediaPipe hands
        
        Args:
            frame: Input frame from camera/NDI
            hand_context: MediaPipe Hands context manager
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
                if (self._resize_buffer is None or 
                    self._resize_buffer.shape[0] != proc_height or 
                    self._resize_buffer.shape[1] != proc_width):
                    self._resize_buffer = np.empty((proc_height, proc_width, 3), dtype=np.uint8)
                
                cv2.resize(frame, (proc_width, proc_height), dst=self._resize_buffer, interpolation=cv2.INTER_LINEAR)
                image = self._resize_buffer
            else:
                image = frame
            
            # Convert to RGB for MediaPipe
            if (self._rgb_buffer is None or 
                self._rgb_buffer.shape[0] != image.shape[0] or 
                self._rgb_buffer.shape[1] != image.shape[1]):
                self._rgb_buffer = np.empty((image.shape[0], image.shape[1], 3), dtype=np.uint8)
            
            cv2.cvtColor(image, cv2.COLOR_BGR2RGB, dst=self._rgb_buffer)
            rgb_image = self._rgb_buffer
            
            # Process with MediaPipe Hands
            results = hand_context.process(rgb_image)
            
            # Convert back to BGR for display
            if self._resize_buffer is not None and self._resize_buffer.shape == rgb_image.shape:
                cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR, dst=self._resize_buffer)
                image = self._resize_buffer
            else:
                image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)
            timestamp = time.time()
            
            hands_detected = bool(results.multi_hand_landmarks)
            
            if hands_detected:
                all_hand_landmarks = []
                all_handedness = []
                
                for i, hand_landmark in enumerate(results.multi_hand_landmarks):
                    hand_landmarks = process_landmarks_to_dict(hand_landmark.landmark, f"hand_{i}")
                    all_hand_landmarks.append(hand_landmarks)
                    
                    # Get handedness
                    if results.multi_handedness and i < len(results.multi_handedness):
                        handedness = results.multi_handedness[i].classification[0].label
                    else:
                        handedness = "Unknown"
                    all_handedness.append(handedness)
                    
                    # Send individual hand data
                    self.send_hand_data(hand_landmarks, handedness, i, timestamp)
                    self.send_hand_bounds_data(hand_landmark.landmark, i)
                
                # Send combined data
                self.send_multiple_hand_data(all_hand_landmarks, all_handedness, timestamp)
                
                # Create landmarks list for bounds
                landmarks_list = [h.landmark for h in results.multi_hand_landmarks]
                self.send_multiple_hand_bounds_data(landmarks_list)
                
                self.osc_sender.send_message("/hand/status", compact_json({"status": len(results.multi_hand_landmarks)}))
                
                # Draw hand landmarks
                for i, hand_landmark in enumerate(results.multi_hand_landmarks):
                    handedness = all_handedness[i] if i < len(all_handedness) else "Unknown"
                    self._draw_landmarks_legacy(image, hand_landmark, handedness)
                
                del all_hand_landmarks
                del all_handedness
            else:
                self.send_empty_hand_data(timestamp)
            
            self.update_fps(backend_name)
            return image
            
        except Exception as e:
            print(f"⚠️  Legacy hand frame processing error: {e}")
            if 'image' in locals() and image is not frame:
                del image
            return frame
    
    def _draw_landmarks_legacy(self, image, hand_landmarks, handedness="Unknown"):
        """
        Draw hand landmarks on image (legacy format)
        
        Args:
            image: Image to draw on
            hand_landmarks: MediaPipe hand landmarks object
            handedness: "Left" or "Right" hand indicator
        """
        display_config = self.config.get('display') if self.config else {}
        hand_config = self.config.get('hand') if self.config else {}
        
        if handedness == "Left":
            landmark_color = tuple(hand_config.get('left_landmark_color', [0, 255, 0]))
            connection_color = tuple(hand_config.get('left_connection_color', [0, 200, 0]))
        else:
            landmark_color = tuple(hand_config.get('right_landmark_color', [255, 0, 0]))
            connection_color = tuple(hand_config.get('right_connection_color', [200, 0, 0]))
        
        landmark_thickness = display_config.get('landmark_thickness', 1)
        landmark_radius = display_config.get('landmark_radius', 2)
        connection_thickness = display_config.get('connection_thickness', 1)
        connection_radius = display_config.get('connection_radius', 1)
        
        mp.solutions.drawing_utils.draw_landmarks(
            image,
            hand_landmarks,
            HAND_CONNECTIONS,
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
