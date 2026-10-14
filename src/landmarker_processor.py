#!/usr/bin/env python3
"""
Landmarker Processing Base Module
Shared MediaPipe Tasks machinery behind the pose, hand and holistic processors:
delegate selection with GPU -> CPU fallback, LIVE_STREAM option building,
detect_async backpressure and taking fresh results from the callback thread.
Subclasses only say which landmarker they wrap, how to build its options and
what to publish/draw for a result.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import time
import platform
import threading
import mediapipe as mp
from mediapipe.framework.formats import landmark_pb2
# Only the drawing helpers and connection tables come from here - inference
# is Tasks-only. (The Tasks package has no equivalent drawing utilities in
# the pinned mediapipe.)
from mediapipe.python.solutions import drawing_utils as _drawing_utils
from mediapipe.python.solutions import hands as _hands_solution
from mediapipe.python.solutions import pose as _pose_solution

from .pose_utils import LetterboxTransform
from .frame_prep import FramePrep

# Optional psutil import for memory monitoring
try:
    import psutil
except ImportError:
    psutil = None

# Platform detection for GPU compatibility
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"

# Landmark connection tables for drawing
POSE_CONNECTIONS = _pose_solution.POSE_CONNECTIONS
HAND_CONNECTIONS = _hands_solution.HAND_CONNECTIONS

#: Returned by setup_processor() when the landmarker could not be built
SETUP_FAILED = (None, None, None, False)


# ============================================================================
# LANDMARKER PROCESSOR BASE CLASS
# ============================================================================
class LandmarkerProcessor:
    """
    Base class for MediaPipe Tasks landmarker processors

    Subclasses set `label` / `backend_suffix`, and implement:
        _download_model()   -> path to the model file
        _landmarker_types() -> (Landmarker class, Options class)
        build_options(delegate) -> Options for that delegate
        _publish(results, target, timestamp) -> OSC + drawing for fresh results
        _draw_results(results, target)       -> redraw stale results
        _report_idle()      -> status 0 on the processor's channels
    """

    #: Name used in log lines ("Pose", "Hand", "Holistic")
    label = "Landmarker"
    #: Appended to the backend name ("GPU (MediaPipe Tasks<suffix>)")
    backend_suffix = ""

    def __init__(self, osc, show_fps=False, config=None, force_cpu=False, force_gpu=False, is_apple_silicon=None):
        """
        Initialize processor

        Args:
            osc: OscEmitter - every OSC message goes out through it
            show_fps: Boolean to enable FPS display
            config: Configuration object
            force_cpu: Force CPU delegate even if GPU available
            force_gpu: Force GPU delegate (WARNING: memory leak on Apple Silicon)
            is_apple_silicon: Override Apple Silicon detection
        """
        self.osc = osc
        self.show_fps = show_fps
        self.config = config
        self.force_cpu = force_cpu
        self.force_gpu = force_gpu
        # Use passed value or detect automatically
        self.is_apple_silicon = is_apple_silicon if is_apple_silicon is not None else IS_APPLE_SILICON
        self.use_gpu = False  # Set during setup

        self.fps_counter = 0
        self.frame_counter = 0
        self.fps_start_time = time.time() if show_fps else None
        self.results = None  # Latest result delivered by the callback
        self.pending_frames = 0  # Frames in MediaPipe's async queue
        # Frames MediaPipe may have in flight before new ones are skipped -
        # 1 keeps latency lowest; performance.max_pending_frames trades
        # latency for throughput on a model that can't keep up
        self.max_pending_frames = max(1, int(config.get('performance', 'max_pending_frames', 1))) if config else 1
        self.skipped_frames = 0  # Count of frames skipped due to backpressure
        self._last_detection_state = False  # Had a detection last time (for transition to empty)
        self._has_fresh_results = False  # Callback delivered new results
        self._display_results = None  # Main-thread-only copy of last taken results for stale drawing
        self._model_path = None

        # Lock protecting results/pending_frames shared with MediaPipe's worker thread
        self._results_lock = threading.Lock()

        # Cache per-frame config lookups (config is not mutated after construction)
        camera_config = config.get('camera') if config else {}
        self._proc_width = camera_config.get('processing_width', 640)
        self._proc_height = camera_config.get('processing_height', 480)

        # Letterbox + RGB conversion for the model input. In all mode without
        # holistic, main.py has the hand processor adopt the pose processor's
        # (share_frame_prep) so each frame is prepared once
        self._frame_prep = FramePrep(self._proc_width, self._proc_height)

        # Maps normalized coords from the (possibly letterboxed) processing
        # frame back to the source frame; identity until the first resize
        self._letterbox_transform = LetterboxTransform(
            1.0, 0, 0, self._proc_width, self._proc_height, self._proc_width, self._proc_height
        )

    # ------------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------------

    def _download_model(self):
        """Return the path of this processor's model file (downloading it if needed)"""
        raise NotImplementedError

    def _landmarker_types(self):
        """Return (Landmarker class, Options class) from mp.tasks.vision"""
        raise NotImplementedError

    def build_options(self, delegate):
        """
        Build the LIVE_STREAM options for one delegate

        Args:
            delegate: mp.tasks.BaseOptions.Delegate.GPU or .CPU

        Returns:
            The landmarker's Options instance (model path is self._model_path)
        """
        raise NotImplementedError

    def _publish(self, results, target, timestamp):
        """Send OSC for, and draw, freshly delivered results"""
        raise NotImplementedError

    def _draw_results(self, results, target):
        """Redraw already-processed results (stale or backpressure frames)"""
        raise NotImplementedError

    def _report_idle(self):
        """Send status 0 on this processor's channels (no fresh detection)"""
        raise NotImplementedError

    def _before_setup(self):
        """
        Hook run before the model is touched

        Returns:
            True to continue; False aborts setup (caller falls back)
        """
        return True

    # ------------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------------

    def _use_gpu_delegate(self):
        """
        Decide whether to try the GPU delegate first

        Returns:
            True to try GPU (then CPU on failure), False for CPU only
        """
        if self.force_cpu:
            print("🔧 Forced CPU delegate via command line")
            return False
        if self.force_gpu:
            print("⚠️  Forced GPU delegate via command line (WARNING: known memory leak on Apple Silicon)")
            return True
        if self.is_apple_silicon:
            # CRITICAL: MediaPipe GPU delegate has a memory leak on Apple Silicon
            # that causes ~1.2MB per frame accumulation. Force CPU to avoid this.
            print("🍎 Apple Silicon detected: Using CPU delegate (GPU has known memory leak)")
            return False
        return True

    def setup_processor(self):
        """
        Setup the MediaPipe Tasks landmarker with GPU/CPU fallback

        Returns:
            Tuple of (landmarker, backend_name, window_title, success);
            (None, None, None, False) when it could not be built
        """
        try:
            if not self._before_setup():
                return SETUP_FAILED

            Landmarker, _ = self._landmarker_types()
            BaseOptions = mp.tasks.BaseOptions

            model_path = self._download_model()
            if not model_path or not os.path.exists(model_path):
                print(f"❌ {self.label} model file not available")
                return SETUP_FAILED
            self._model_path = model_path

            landmarker = None
            backend_name = None

            # Try GPU delegate first (unless forced to CPU or on Apple Silicon)
            if self._use_gpu_delegate():
                print(f"🎯 Attempting GPU delegate for {self.label.lower()} tracking...")
                try:
                    landmarker = Landmarker.create_from_options(self.build_options(BaseOptions.Delegate.GPU))
                    backend_name = f"GPU (MediaPipe Tasks{self.backend_suffix})"
                    self.use_gpu = True
                    print(f"✅ GPU delegate initialized successfully for {self.label.lower()} tracking")
                    if self.is_apple_silicon:
                        print("   Using SRGBA image format for Apple Silicon Metal compatibility")
                except Exception as gpu_error:
                    print(f"⚠️  GPU delegate failed during initialization: {gpu_error}")
                    landmarker = None

            # Fallback to CPU delegate if GPU failed or was not attempted
            if landmarker is None:
                print(f"🔄 Using CPU delegate for {self.label.lower()} tracking...")
                try:
                    landmarker = Landmarker.create_from_options(self.build_options(BaseOptions.Delegate.CPU))
                    backend_name = f"CPU (MediaPipe Tasks{self.backend_suffix})"
                    self.use_gpu = False
                    print(f"✅ CPU delegate initialized successfully for {self.label.lower()} tracking")
                except Exception as cpu_error:
                    print(f"❌ CPU delegate also failed: {cpu_error}")
                    return SETUP_FAILED

            window_title = f"MediaPipe Tasks {self.label} Detection"
            print(f"✅ Successfully initialized {backend_name}")
            return landmarker, backend_name, window_title, True

        except ImportError as e:
            print(f"⚠️  MediaPipe Tasks not available: {e}")
            return SETUP_FAILED
        except Exception as e:
            print(f"❌ Failed to initialize MediaPipe Tasks ({self.label.lower()}): {e}")
            return SETUP_FAILED

    def share_frame_prep(self, other):
        """
        Adopt another processor's FramePrep so each frame is letterboxed
        and colour-converted once, not once per processor (#36)

        Args:
            other: The processor whose FramePrep to adopt (the pose processor)
        """
        other._frame_prep.shared = True
        self._frame_prep = other._frame_prep

    # ------------------------------------------------------------------------
    # Async result callback
    # ------------------------------------------------------------------------

    def _result_callback(self, result, output_image, timestamp_ms):
        """
        Callback for async detection results from MediaPipe Tasks
        Runs on MediaPipe's worker thread - state updates guarded by lock
        Note: We only store the result, not the output_image to avoid memory leaks
        """
        with self._results_lock:
            self.results = result
            self._has_fresh_results = True  # Mark that we have new results to process
            self.pending_frames = max(0, self.pending_frames - 1)
        # output_image is deliberately not stored - only the result is needed

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
                    osc_stats = self.osc.get_stats()
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Memory: {mem_mb:.1f}MB | "
                          f"OSC Sent: {osc_stats['sent']} Dropped: {osc_stats['dropped']} Queued: {osc_stats['queued']} | "
                          f"MP Pending: {self.pending_frames} Skipped: {self.skipped_frames}")
                else:
                    print(f"{backend_name} FPS: {actual_fps:.2f} | Skipped: {self.skipped_frames}")
                self.fps_start_time = fps_end_time

    # ------------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------------

    def _make_mp_image(self):
        """Wrap the prepared frame for the model (SRGBA for Metal on Apple Silicon GPU)"""
        # The Metal GPU buffer doesn't support SRGB (3 channels), only SRGBA
        if self.is_apple_silicon and self.use_gpu:
            return mp.Image(image_format=mp.ImageFormat.SRGBA, data=self._frame_prep.rgba())
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=self._frame_prep.rgb())

    def process_frame(self, frame, landmarker, backend_name, timestamp_counter, draw_target=None):
        """
        Process a single frame with MediaPipe Tasks
        Handles letterboxing, colour conversion, backpressure and OSC output

        Args:
            frame: Input frame from camera/NDI
            landmarker: MediaPipe landmarker instance
            backend_name: Backend name for FPS display
            timestamp_counter: Frame counter for async processing
            draw_target: Optional shared display array to draw landmarks into
                instead of the inference frame. Lets a caller composite this
                processor's overlays onto another processor's output (e.g.
                pose + hand in one preview) without feeding annotated pixels
                back into either model. Model input is always the clean
                letterboxed frame, never draw_target. Defaults to None,
                which draws into (a copy of, if shared) the letterboxed frame.

        Returns:
            Annotated frame with landmarks drawn (draw_target, if provided)
        """
        try:
            if frame is None or frame.size == 0:
                return frame

            # Letterbox to the processing size (aspect-preserving, so OSC
            # coordinates stay correct relative to the source frame) - shared
            # with the other processor reading this frame, see FramePrep
            image, self._letterbox_transform = self._frame_prep.letterbox(frame)

            # `image` is the inference input ONLY - always the clean,
            # letterboxed frame, never annotated. `target` is what gets
            # drawn into and returned.
            target = draw_target if draw_target is not None else self._frame_prep.drawable(image)

            # Check if MediaPipe's async queue is backing up - skip frame if too many pending
            if self.pending_frames >= self.max_pending_frames:
                # Skip MediaPipe processing, but keep the preview skeleton
                # alive by redrawing the last known results - otherwise it
                # blinks on/off every time the model is the bottleneck
                self.skipped_frames += 1
                self.update_fps(backend_name)
                if self._display_results is not None:
                    self._draw_results(self._display_results, target)
                # No OSC here - a skipped frame means "no new information",
                # not "nothing detected"; sending status would misrepresent one or the other
                return target

            # Process with MediaPipe Tasks (async)
            landmarker.detect_async(self._make_mp_image(), timestamp_counter)
            with self._results_lock:
                self.pending_frames += 1

            timestamp = time.time()

            # Atomically check-and-take fresh results from the callback thread
            # (hold the lock only for the swap - never during serialization/sends/drawing)
            fresh_results = None
            with self._results_lock:
                if self._has_fresh_results and self.results is not None:
                    fresh_results = self.results
                    self.results = None
                    self._has_fresh_results = False

            # Only process and send OSC when we have fresh results from the callback
            # This ensures OSC messages are synchronized with actual detection rate
            if fresh_results is not None:
                # Keep for stale drawing on frames before the next callback lands (main thread only)
                self._display_results = fresh_results
                self._publish(fresh_results, target, timestamp)
            elif self._display_results is not None:
                # Results already processed - just redraw them
                self._draw_results(self._display_results, target)
                # No fresh detection this frame - status 0 signals no actively tracked person
                self._report_idle()
            else:
                # No results yet - still send status so receivers know program is running
                self._report_idle()

            self.update_fps(backend_name)
            return target

        except Exception as e:
            print(f"⚠️  {self.label} frame processing error: {e}")
            # Clear results on error to prevent memory leak (under lock - shared with callback thread)
            with self._results_lock:
                self.results = None
                self._has_fresh_results = False
            self._display_results = None
            return draw_target if draw_target is not None else frame

    # ------------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------------

    @staticmethod
    def _make_spec(color, thickness, radius):
        """Build a DrawingSpec for landmark or connection rendering"""
        return _drawing_utils.DrawingSpec(color=tuple(color), thickness=thickness, circle_radius=radius)

    def _build_hand_specs(self):
        """Pre-build left/right hand DrawingSpec pairs (left = green, right = red by default)"""
        display_config = self.config.get('display') if self.config else {}
        hand_config = self.config.get('hand') if self.config else {}
        thickness = display_config.get('landmark_thickness', 1)
        radius = display_config.get('landmark_radius', 2)
        conn_thickness = display_config.get('connection_thickness', 1)
        conn_radius = display_config.get('connection_radius', 1)
        self._left_landmark_spec = self._make_spec(hand_config.get('left_landmark_color', [0, 255, 0]), thickness, radius)
        self._left_connection_spec = self._make_spec(hand_config.get('left_connection_color', [0, 200, 0]), conn_thickness, conn_radius)
        self._right_landmark_spec = self._make_spec(hand_config.get('right_landmark_color', [255, 0, 0]), thickness, radius)
        self._right_connection_spec = self._make_spec(hand_config.get('right_connection_color', [200, 0, 0]), conn_thickness, conn_radius)

    def _draw_connected(self, image, landmarks, connections, landmark_spec, connection_spec):
        """
        Draw a landmark list and its connections onto image

        Args:
            image: Image to draw on
            landmarks: Tasks landmark list (objects with x, y, z)
            connections: POSE_CONNECTIONS or HAND_CONNECTIONS
            landmark_spec, connection_spec: DrawingSpecs for points and lines
        """
        proto = landmark_pb2.NormalizedLandmarkList()
        proto.landmark.extend([
            landmark_pb2.NormalizedLandmark(x=landmark.x, y=landmark.y, z=landmark.z)
            for landmark in landmarks
        ])
        _drawing_utils.draw_landmarks(image, proto, connections, landmark_spec, connection_spec)
