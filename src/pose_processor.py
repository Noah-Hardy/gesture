"""
Pose processing module with GPU and CPU implementations.
"""
import os
import time
import json
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.framework.formats import landmark_pb2

from .pose_utils import get_pose_bounds_with_values, process_landmarks_to_dict
from .model_downloader import download_pose_model


class PoseProcessor:
    """Base class for pose processing"""
    
    def __init__(self, osc_sender, show_fps=False, config=None):
        self.osc_sender = osc_sender
        self.show_fps = show_fps
        self.config = config
        self.fps_counter = 0
        self.fps_start_time = time.time() if show_fps else None
        
    def setup_gpu_processor(self):
        """Setup GPU-accelerated MediaPipe processor"""
        try:
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
            
            model_path = download_pose_model()
            
            if model_path and os.path.exists(model_path):
                # Get MediaPipe configuration
                mp_config = self.config.get('mediapipe') if self.config else {}
                
                # For Apple Silicon, force CPU delegate to avoid GPU buffer format issues
                import platform
                try:
                    machine = platform.machine().lower()
                    system = platform.system().lower()
                    is_apple_silicon = (machine == 'arm64' and system == 'darwin')
                    
                    if is_apple_silicon:
                        print("🍎 Apple Silicon detected - using CPU delegate to avoid GPU buffer issues")
                        use_cpu_delegate = True
                        base_options = python.BaseOptions(
                            model_asset_path=model_path,
                            delegate=python.BaseOptions.Delegate.CPU
                        )
                    else:
                        # Try GPU delegate first for non-Apple Silicon
                        try:
                            base_options = python.BaseOptions(
                                model_asset_path=model_path,
                                delegate=python.BaseOptions.Delegate.GPU
                            )
                            print("🎯 GPU delegate configured with model file")
                            use_cpu_delegate = False
                        except Exception as gpu_error:
                            print(f"⚠️  GPU delegate failed, trying CPU delegate: {gpu_error}")
                            base_options = python.BaseOptions(
                                model_asset_path=model_path,
                                delegate=python.BaseOptions.Delegate.CPU
                            )
                            print("🔄 CPU delegate configured with model file")
                            use_cpu_delegate = True
                except Exception as platform_error:
                    print(f"⚠️  Platform detection failed: {platform_error}")
                    # Default to CPU delegate if platform detection fails
                    use_cpu_delegate = True
                    base_options = python.BaseOptions(
                        model_asset_path=model_path,
                        delegate=python.BaseOptions.Delegate.CPU
                    )
                    print("🔄 CPU delegate configured with model file (fallback)")
                
                pose_landmarker_options = vision.PoseLandmarkerOptions(
                    base_options=base_options,
                    running_mode=vision.RunningMode.VIDEO,
                    num_poses=1,
                    min_pose_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
                    min_pose_presence_confidence=mp_config.get('min_pose_presence_confidence', 0.5),
                    min_tracking_confidence=mp_config.get('min_tracking_confidence', 0.5)
                )
                pose_landmarker = vision.PoseLandmarker.create_from_options(pose_landmarker_options)
                
                backend_name = "CPU (MediaPipe Tasks)" if use_cpu_delegate else "GPU (MediaPipe Tasks)"
                window_title = "CPU-Tasks Pose Detection" if use_cpu_delegate else "GPU-Accelerated Pose Detection"
                
                print(f"✅ Successfully initialized {backend_name}")
                return pose_landmarker, backend_name, window_title, True
            else:
                print("❌ Model file not available")
                return None, None, None, False
        except ImportError as e:
            print(f"GPU delegate not available: {e}")
            return None, None, None, False
        except Exception as e:
            print(f"❌ Failed to initialize GPU MediaPipe: {e}")
            return None, None, None, False
    
    def setup_cpu_processor(self):
        """Setup CPU-based MediaPipe processor"""
        mp_pose = mp.solutions.pose
        
        # Get MediaPipe configuration
        mp_config = self.config.get('mediapipe') if self.config else {}
        
        # Check if we're on Apple Silicon for Metal acceleration
        import platform
        try:
            machine = platform.machine().lower()
            system = platform.system().lower()
            is_apple_silicon = (machine == 'arm64' and system == 'darwin')
            
            if is_apple_silicon:
                backend_name = "CPU + Metal GPU (Legacy)"
                window_title = "Metal-Accelerated Pose Detection"
                print("✅ Using CPU-based MediaPipe with Metal GPU acceleration")
            else:
                backend_name = "CPU Only"
                window_title = "CPU Pose Detection"
                print("ℹ️  Using CPU-only MediaPipe")
        except Exception as platform_error:
            print(f"⚠️  Platform detection failed: {platform_error}")
            # Default to CPU-only if platform detection fails
            backend_name = "CPU Only"
            window_title = "CPU Pose Detection"
            print("ℹ️  Using CPU-only MediaPipe (fallback)")
        
        pose_context = mp_pose.Pose(
            static_image_mode=False,
            model_complexity=mp_config.get('model_complexity', 0),
            smooth_landmarks=mp_config.get('smooth_landmarks', True),
            enable_segmentation=mp_config.get('enable_segmentation', False),
            smooth_segmentation=False,
            min_detection_confidence=mp_config.get('min_detection_confidence', 0.7),
            min_tracking_confidence=mp_config.get('min_tracking_confidence', 0.5)
        )
        
        return pose_context, backend_name, window_title
    
    def send_pose_data(self, pose_landmarks, pose_world_landmarks, timestamp):
        """Send pose data via OSC"""
        if pose_landmarks:
            osc_payload = {
                "timestamp": timestamp,
                "landmarks": pose_landmarks
            }
            self.osc_sender.send_message("/pose/raw", json.dumps(osc_payload))
        
        if pose_world_landmarks:
            world_payload = {
                "timestamp": timestamp,
                "landmarks": pose_world_landmarks
            }
            self.osc_sender.send_message("/pose/world", json.dumps(world_payload))
    
    def send_bounds_data(self, landmarks, world_landmarks):
        """Send bounds data via OSC"""
        if landmarks:
            bounds = get_pose_bounds_with_values(landmarks)
            self.osc_sender.send_message("/pose/raw_bounds", json.dumps(bounds))
        
        if world_landmarks:
            world_bounds = get_pose_bounds_with_values(world_landmarks)
            self.osc_sender.send_message("/pose/world_bounds", json.dumps(world_bounds))
    
    def send_empty_data(self, timestamp):
        """Send empty data to clear cache on receiving machine"""
        empty_payload = {
            "timestamp": timestamp,
            "landmarks": []
        }
        self.osc_sender.send_message("/pose/raw", json.dumps(empty_payload))
        self.osc_sender.send_message("/pose/raw_bounds", json.dumps({}))
        self.osc_sender.send_message("/pose/world", json.dumps(empty_payload))
        self.osc_sender.send_message("/pose/world_bounds", json.dumps({}))
        self.osc_sender.send_message("/mp/status", json.dumps({"status": 0}))
    
    def update_fps(self, backend_name):
        """Update and display FPS if enabled"""
        if self.show_fps:
            self.fps_counter += 1
            if self.fps_counter % 30 == 0:
                fps_end_time = time.time()
                actual_fps = 30 / (fps_end_time - self.fps_start_time)
                print(f"{backend_name} FPS: {actual_fps:.2f}")
                self.fps_start_time = fps_end_time


class GPUPoseProcessor(PoseProcessor):
    """GPU-accelerated pose processor"""
    
    def process_frame(self, frame, pose_landmarker, backend_name):
        """Process a single frame with GPU acceleration"""
        try:
            # Validate and prepare frame
            if frame is None or frame.size == 0:
                print("⚠️  Invalid frame received")
                return frame
            
            # Ensure frame is in the correct format (BGR -> RGB)
            if len(frame.shape) != 3 or frame.shape[2] != 3:
                print(f"⚠️  Unexpected frame shape: {frame.shape}")
                return frame
            
            # Convert to RGB for MediaPipe with proper format handling
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Ensure the frame is contiguous and has the right dtype
            if not rgb_frame.flags['C_CONTIGUOUS']:
                rgb_frame = np.ascontiguousarray(rgb_frame)
            
            # Ensure uint8 format
            if rgb_frame.dtype != np.uint8:
                rgb_frame = rgb_frame.astype(np.uint8)
            
            # Create MediaPipe Image with explicit format specification
            try:
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            except Exception as img_error:
                print(f"⚠️  MediaPipe Image creation failed: {img_error}")
                # Fall back to CPU processing for this frame
                raise Exception(f"GPU image format error: {img_error}")
            
            # Process with GPU-accelerated MediaPipe
            pose_landmarker_result = pose_landmarker.detect_for_video(
                mp_image, timestamp_ms=int(time.time() * 1000)
            )
            
            # Convert back to BGR
            image = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
            timestamp = time.time()
            
            pose_detected = bool(pose_landmarker_result.pose_landmarks)
            
            if pose_detected:
                # Process landmarks
                pose_landmarks = process_landmarks_to_dict(
                    pose_landmarker_result.pose_landmarks[0], "pose"
                )
                
                pose_world_landmarks = []
                if (hasattr(pose_landmarker_result, 'pose_world_landmarks') and 
                    pose_landmarker_result.pose_world_landmarks):
                    pose_world_landmarks = process_landmarks_to_dict(
                        pose_landmarker_result.pose_world_landmarks[0], "pose_world"
                    )
                
                # Send data
                self.send_pose_data(pose_landmarks, pose_world_landmarks, timestamp)
                self.send_bounds_data(
                    pose_landmarker_result.pose_landmarks[0],
                    pose_landmarker_result.pose_world_landmarks[0] if pose_world_landmarks else None
                )
                
                self.osc_sender.send_message("/mp/status", json.dumps({"status": 1}))
                
                # Draw pose landmarks
                self._draw_gpu_landmarks(image, pose_landmarker_result.pose_landmarks[0])
            else:
                self.send_empty_data(timestamp)
            
            self.update_fps(backend_name)
            return image
            
        except Exception as e:
            print(f"⚠️  GPU frame processing error: {e}")
            # Return the original frame without processing
            return frame
    
    def _draw_gpu_landmarks(self, image, landmarks):
        """Draw landmarks for GPU processor"""
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
        for landmark in landmarks:
            pose_landmarks_proto.landmark.add(
                x=landmark.x,
                y=landmark.y,
                z=landmark.z,
                visibility=landmark.visibility if hasattr(landmark, "visibility") else 1.0
            )
        
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


class CPUPoseProcessor(PoseProcessor):
    """CPU-based pose processor"""
    
    def process_frame(self, frame, pose_context, backend_name):
        """Process a single frame with CPU"""
        try:
            # Convert to RGB
            image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Process with MediaPipe Pose
            results = pose_context.process(image)
            
            # Convert back to BGR
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
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
                
                self.osc_sender.send_message("/mp/status", json.dumps({"status": 1}))
                
                # Draw pose landmarks
                if results.pose_landmarks:
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
                        results.pose_landmarks,
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
            else:
                self.send_empty_data(timestamp)
            
            self.update_fps(backend_name)
            return image
            
        except Exception as e:
            print(f"⚠️  CPU frame processing error: {e}")
            # Return the original frame without processing
            return frame
