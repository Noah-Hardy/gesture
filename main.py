#!/usr/bin/env python3
"""
MediaPipe Pose Detection with OSC Output
Main entry point for pose tracking with network streaming
"""

# ============================================================================
# IMPORTS
# ============================================================================
import cv2
import argparse
import os
import platform
import sys
import time
from pythonosc import udp_client

# Import modular components
from src import ThreadedOSCSender, TasksPoseProcessor, LegacyPoseProcessor, get_config
from src import TasksHandProcessor, LegacyHandProcessor
from src import TasksHolisticProcessor
from src import NDICapture, list_ndi_sources, NDI_AVAILABLE


# ============================================================================
# PLATFORM DETECTION
# ============================================================================
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"


# ============================================================================
# EXIT CODES
# ============================================================================
# Setup failures that happen before the processing loop even starts (camera
# open failure, no processor could be constructed, mismatched backends, bad
# OSC target, ...) keep using the bare `1` they already used - that's out of
# scope here. These are specifically for how run() reports *why the
# processing loop itself ended*, so the launcher can tell a clean stop from
# a real failure instead of treating every nonzero code the same way.
EXIT_OK = 0                # Clean stop: user Stop (SIGINT), 'q', window closed
EXIT_CAPTURE_LOST = 2      # Consecutive frame-read failures tripped the loop's guard
EXIT_CRASH = 3             # Unhandled exception inside the processing loop


# ============================================================================
# COMMAND LINE ARGUMENT PARSING
# ============================================================================
def build_parser():
    """
    Build the command line argument parser

    Returns:
        argparse.ArgumentParser configured with all supported options
    """
    parser = argparse.ArgumentParser(description='MediaPipe Pose Detection with OSC')
    parser.add_argument('--fps', action='store_true', help='Show FPS counter (overrides config)')
    parser.add_argument('--config', default=None, help='Configuration file path (default: config.json)')
    parser.add_argument('--create-config', action='store_true', help='Create default configuration file and exit')
    parser.add_argument('--show-config', action='store_true', help='Show current configuration and exit')
    parser.add_argument('--host', help='OSC host address (overrides config)')
    parser.add_argument('--port', type=int, help='OSC port (overrides config)')
    parser.add_argument('--camera', type=int, help='Camera device ID (overrides config)')
    parser.add_argument('--force-cpu', action='store_true', help='Force CPU delegate (skip GPU)')
    parser.add_argument('--force-gpu', action='store_true', help='Force GPU delegate (WARNING: has memory leak on Apple Silicon)')
    parser.add_argument('--force-legacy', action='store_true', help='Force Legacy MediaPipe (skip Tasks API)')
    parser.add_argument('--ndi', action='store_true', help='Use NDI input instead of camera')
    parser.add_argument('--ndi-source', type=str, help='NDI source name to connect to')
    parser.add_argument('--list-ndi', action='store_true', help='List available NDI sources and exit')
    parser.add_argument('--pose-model', choices=['lite', 'full', 'heavy'], default=None, help='Pose model type: lite (fastest), full (balanced), or heavy (most accurate). If not set, uses config value (config default: lite)')
    parser.add_argument('--fps-cap', type=int, help='Cap frame rate for stability (e.g., 30). If not set, runs uncapped.')
    parser.add_argument('--no-holistic', action='store_true', help='In all mode, use separate pose + hand landmarkers instead of the holistic landmarker')
    mirror_group = parser.add_mutually_exclusive_group()
    mirror_group.add_argument('--mirror', dest='mirror_preview', action='store_true', default=None,
                              help='Mirror the preview window horizontally (display only - OSC coordinates are unchanged)')
    mirror_group.add_argument('--no-mirror', dest='mirror_preview', action='store_false',
                              help='Do not mirror the preview window (overrides the config value)')
    parser.add_argument('mode', choices=['pose', 'hand', 'all'], help='Tracking mode: pose, hand, or all (both)')
    return parser


def parse_args(argv=None):
    """
    Parse command line arguments

    Args:
        argv: Argument list to parse (defaults to sys.argv[1:])

    Returns:
        argparse.Namespace of parsed arguments
    """
    return build_parser().parse_args(argv)


# ============================================================================
# CONFIGURATION LOADING AND OVERRIDES
# ============================================================================
def apply_config_overrides(args, config):
    """
    Apply command line argument overrides on top of the loaded configuration

    Args:
        args: Parsed command line arguments
        config: Configuration object to mutate
    """
    if args.config:
        config.config_file = args.config
        config.config = config._load_config()

    # Apply command line argument overrides
    if args.fps:
        config.set('performance', 'show_fps', True)
    if args.host:
        config.set('osc', 'host', args.host)
    if args.port is not None:
        config.set('osc', 'port', args.port)
    if args.camera is not None:
        config.set('camera', 'device_id', args.camera)
    if args.pose_model:
        config.set('mediapipe', 'pose_model_type', args.pose_model)
    if args.fps_cap is not None:
        config.set('performance', 'target_fps', args.fps_cap)
    if args.mirror_preview is not None:
        config.set('display', 'mirror_preview', args.mirror_preview)


# ============================================================================
# HANDLE UTILITY COMMANDS (exit after execution)
# ============================================================================
def handle_utility_commands(args, config):
    """
    Run one-shot utility commands that exit instead of starting tracking

    Args:
        args: Parsed command line arguments
        config: Configuration object

    Returns:
        True if a utility command ran and the application should exit
    """
    if args.create_config:
        config.create_default_config_file()
        return True

    if args.show_config:
        config.print_config()
        return True

    if args.list_ndi:
        if NDI_AVAILABLE:
            sources = list_ndi_sources()
            if sources:
                print(f"Found {len(sources)} NDI source(s):")
                for name in sources:
                    print(f"  - {name}")
            else:
                print("No NDI sources found on network")
        else:
            print("NDI library not available. Install with: uv add ndi-python")
        return True

    return False


# ============================================================================
# PLATFORM AND GPU INFORMATION
# ============================================================================
def print_platform_info():
    """Print platform detection details"""
    print(f"🖥️  Platform: {platform.system()} {platform.machine()}")
    if IS_APPLE_SILICON:
        print("🍎 Apple Silicon detected - using SRGBA format for GPU compatibility")


# ============================================================================
# CAMERA/NDI CAPTURE SETUP
# ============================================================================
def setup_camera(config, use_ndi=False, ndi_source=None):
    """
    Initialize video capture from camera or NDI source
    
    Args:
        config: Configuration object with camera settings
        use_ndi: Boolean to use NDI instead of camera
        ndi_source: Name of NDI source to connect to
        
    Returns:
        cv2.VideoCapture or NDICapture object when a capture was opened.
        None when NDI was explicitly requested and definitively failed
        (library missing, no source found, or setup raised) - the caller
        must treat that as a hard failure and NOT fall back to the webcam,
        since the webcam's warm-up read is what triggers an unwanted
        macOS camera-permission prompt for a user who asked for NDI.
    """
    camera_config = config.get('camera')

    # Determine if NDI should be used (command line or config)
    use_ndi = use_ndi or camera_config.get('use_ndi', False)
    ndi_source = ndi_source or camera_config.get('ndi_source')

    # ------------------------------------------------------------------------
    # NDI capture if requested - any failure here is a hard error, not a
    # silent fallback to the webcam (see docstring above).
    # ------------------------------------------------------------------------
    if use_ndi:
        if not NDI_AVAILABLE:
            print("❌ NDI requested but ndi-python not installed")
            print("   Install with: uv add ndi-python")
            return None

        print("🎬 Setting up NDI capture...")
        try:
            cap = NDICapture(source_name=ndi_source)
        except Exception as e:
            print(f"❌ NDI setup failed: {e}")
            print("   Check that ndi-python and the NDI runtime are installed correctly")
            return None

        if cap.isOpened():
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            proc_w = camera_config.get('processing_width', 640)
            proc_h = camera_config.get('processing_height', 480)
            if actual_w != proc_w or actual_h != proc_h:
                print(f"📐 NDI: {actual_w}x{actual_h} → processing at {proc_w}x{proc_h}")
            return cap

        print("❌ NDI source unavailable - not falling back to webcam")
        print("   Check the source name and confirm it is broadcasting on the network")
        try:
            cap.release()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------------
    # Standard OpenCV camera capture
    # ------------------------------------------------------------------------
    cap = cv2.VideoCapture(camera_config['device_id'])
    cap.set(cv2.CAP_PROP_FPS, camera_config['fps'])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, camera_config['buffer_size'])
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, camera_config['width'])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_config['height'])
    
    print(f"📷 Camera setup: Device {camera_config['device_id']}, "
          f"{camera_config['width']}x{camera_config['height']} @ {camera_config['fps']}fps")
    
    # ------------------------------------------------------------------------
    # Wait for camera initialization (important for virtual cameras)
    # ------------------------------------------------------------------------
    print("⏳ Waiting for camera to initialize...")
    for i in range(30):  # Try for up to 3 seconds
        ret, frame = cap.read()
        if ret and frame is not None:
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = cap.get(cv2.CAP_PROP_FPS)
            print(f"✅ Camera ready after {i * 0.1:.1f}s")
            print(f"📐 Actual resolution: {actual_w}x{actual_h} @ {actual_fps}fps")
            
            if actual_w != camera_config['width'] or actual_h != camera_config['height']:
                print(f"⚠️  Resolution differs from config ({camera_config['width']}x{camera_config['height']})")
                print(f"   Frames will be resized to {camera_config.get('processing_width', 640)}x"
                      f"{camera_config.get('processing_height', 480)} for processing")
            break
        time.sleep(0.1)
    else:
        print("⚠️  Camera may be slow to start - continuing anyway")
    
    return cap


# ============================================================================
# PREVIEW WINDOW
# ============================================================================
def show_preview(image, window_title, mirror):
    """
    Draw the annotated frame in the preview window

    The flip happens after processing, so it only affects what is displayed -
    landmark coordinates and every OSC message stay in source space.

    Args:
        image: Annotated frame to display
        window_title: Preview window name
        mirror: Boolean to flip the preview horizontally (webcam mirror view)
    """
    if mirror:
        image = cv2.flip(image, 1)
    cv2.imshow(window_title, image)


# ============================================================================
# LEGACY PROCESSING LOOP HELPER
# ============================================================================
def _legacy_loop(cap, pose_processor, pose_ctx, hand_processor, hand_ctx,
                 display_config, window_title, max_consecutive_failures, show_fps, tracking_mode,
                 frame_interval=0, reassert_dock_policy=None):
    """
    Helper function to run the legacy processing loop
    Handles both single and combined processor modes

    Args:
        frame_interval: Minimum time between frames (0 = uncapped)
        reassert_dock_policy: Optional callable (macOS, launcher-spawned only)
            that re-applies the Accessory Dock policy after HighGUI's first
            window creation resets it. None elsewhere.

    Returns:
        One of the EXIT_* constants indicating why the loop ended, so run()
        (which invokes this three different ways depending on which
        processors are active) can report the real reason instead of
        assuming every exit was a clean one.
    """
    consecutive_failures = 0
    last_frame_time = time.time()
    mirror_preview = display_config.get('mirror_preview', False)
    exit_reason = EXIT_OK

    while cap.isOpened():
        # Frame rate limiting
        if frame_interval > 0:
            current_time = time.time()
            elapsed = current_time - last_frame_time
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)
            last_frame_time = time.time()
        
        ret, frame = cap.read()
        if not ret:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                print(f"❌ Too many consecutive frame failures ({consecutive_failures})")
                exit_reason = EXIT_CAPTURE_LOST
                break
            continue

        consecutive_failures = 0

        try:
            # Each processor's inference always reads the clean source
            # `frame` directly, never a previous processor's annotated
            # output - otherwise the second processor's model would see the
            # first processor's skeleton painted over the pixels it's about
            # to analyze, and would letterbox an already-letterboxed frame
            # (an identity transform) instead of the true source frame,
            # breaking its OSC coordinate mapping when processing/source
            # aspect ratios differ. `display` accumulates both processors'
            # overlays onto one shared array instead.
            display = None

            # Process pose if enabled
            if pose_processor and pose_ctx:
                display = pose_processor.process_frame(frame, pose_ctx, "Pose", draw_target=display)

            # Process hand if enabled
            if hand_processor and hand_ctx:
                display = hand_processor.process_frame(frame, hand_ctx, "Hand", draw_target=display)

            if display_config.get('show_window', True):
                show_preview(display, window_title, mirror_preview)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
                # Re-fight HighGUI for the Dock policy right after it has
                # had its chance to reset it on window creation;
                # reassert_accessory_policy() caps itself after a few
                # calls, so this is cheap once it wins.
                if reassert_dock_policy is not None:
                    reassert_dock_policy()
                # The red close button destroys the window; without this
                # check the loop would keep running and imshow would
                # recreate it on the next frame.
                try:
                    if cv2.getWindowProperty(window_title, cv2.WND_PROP_VISIBLE) < 1:
                        print("🛑 Preview window closed - stopping")
                        break
                except cv2.error:
                    break

        except Exception as frame_error:
            print(f"⚠️  Legacy frame processing error: {frame_error}")
            continue

    return exit_reason


# ============================================================================
# MAIN APPLICATION FUNCTION
# ============================================================================
def run(args, config):
    """
    Main application loop
    Initializes OSC, camera, and pose processor, then runs processing loop

    Args:
        args: Parsed command line arguments
        config: Configuration object

    Returns:
        Process exit code (0 on success)
    """
    # ------------------------------------------------------------------------
    # Get configuration sections
    # ------------------------------------------------------------------------
    osc_config = config.get('osc')
    performance_config = config.get('performance')
    display_config = config.get('display')

    # ------------------------------------------------------------------------
    # Dock policy reassert (macOS, launcher-spawned only)
    # ------------------------------------------------------------------------
    # HighGUI resets the Accessory policy set in main() the moment it creates
    # its first preview window, so the loops below re-apply it right after
    # their first cv2.waitKey() following the first cv2.imshow(). Lazily
    # imported and gated the same way as set_accessory_policy() so CLI/
    # non-GUI runs never touch libobjc.
    reassert_dock_policy = None
    if os.environ.get('MPOSC_LAUNCHED_FROM_GUI'):
        from src.macos_app import reassert_accessory_policy
        reassert_dock_policy = reassert_accessory_policy

    # ------------------------------------------------------------------------
    # Initialize OSC communication
    # ------------------------------------------------------------------------
    print(f"🌐 OSC Target: {osc_config['host']}:{osc_config['port']}")
    try:
        osc_client = udp_client.SimpleUDPClient(osc_config['host'], osc_config['port'])
        threaded_osc = ThreadedOSCSender(osc_client, queue_size=osc_config['queue_size'])
    except OSError as e:
        print(f"❌ Could not resolve OSC target {osc_config['host']}:{osc_config['port']}: {e}")
        print("   Check the OSC host address for typos")
        print("   If using a hostname, try the IP address instead")
        return 1
    
    # ------------------------------------------------------------------------
    # Frame rate limiting setup
    # ------------------------------------------------------------------------
    target_fps = performance_config.get('target_fps', 0)
    if target_fps > 0:
        frame_interval = 1.0 / target_fps
        print(f"⏱️  Frame rate capped at {target_fps} FPS ({frame_interval*1000:.1f}ms/frame)")
    else:
        frame_interval = 0
        print("⏱️  Frame rate: uncapped")
    
    # ------------------------------------------------------------------------
    # Setup camera or NDI capture
    # ------------------------------------------------------------------------
    cap = setup_camera(config, use_ndi=args.ndi, ndi_source=args.ndi_source)
    if cap is None:
        # setup_camera already printed a specific ❌ error (NDI was requested
        # and definitively failed) - do not fall back to the webcam.
        return 1

    # ------------------------------------------------------------------------
    # Initialize processor(s) based on mode (pose/hand/all)
    # ------------------------------------------------------------------------
    show_fps = performance_config['show_fps']
    tracking_mode = args.mode
    
    # Determine processing strategy
    use_tasks = not args.force_legacy
    force_cpu = args.force_cpu
    force_gpu = args.force_gpu
    timestamp_counter = 0
    
    # Processor containers
    pose_processor = None
    pose_landmarker = None
    pose_is_tasks = False
    
    hand_processor = None
    hand_landmarker = None
    hand_is_tasks = False
    
    backend_names = []
    window_title = "MediaPipe OSC Detection"
    
    # ------------------------------------------------------------------------
    # Setup Holistic Processor (preferred for 'all' mode - pose + hands in
    # a single model pass instead of two landmarkers per frame)
    # ------------------------------------------------------------------------
    holistic_active = False
    if tracking_mode == 'all' and use_tasks and not args.no_holistic:
        num_poses = config.get('mediapipe', 'num_poses', 1)
        if num_poses > 1:
            print("⚠️  Holistic landmarker is single-person (num_poses > 1 configured)")
            print("   Falling back to separate pose + hand landmarkers")
        else:
            print("🧍 Initializing holistic tracking (pose + hands, one model)...")
            try:
                holistic_processor = TasksHolisticProcessor(
                    threaded_osc,
                    show_fps=show_fps,
                    config=config,
                    force_cpu=force_cpu,
                    force_gpu=force_gpu,
                    is_apple_silicon=IS_APPLE_SILICON
                )
                holistic_landmarker, holistic_backend, _, success = holistic_processor.setup_processor()
                if success:
                    # Holistic drives the same loop slot as the pose processor;
                    # it publishes both pose and hand OSC channels itself
                    pose_processor = holistic_processor
                    pose_landmarker = holistic_landmarker
                    pose_is_tasks = True
                    holistic_active = True
                    backend_names.append(holistic_backend)
                    print("✅ Using MediaPipe Tasks (Holistic)")
                else:
                    print("⚠️  Holistic setup failed, falling back to separate pose + hand landmarkers")
            except Exception as e:
                print(f"⚠️  Holistic processor failed: {e}")
                print("   Falling back to separate pose + hand landmarkers")

    # ------------------------------------------------------------------------
    # Setup Pose Processor (if mode is 'pose' or 'all')
    # ------------------------------------------------------------------------
    if tracking_mode in ['pose', 'all'] and not holistic_active:
        print("🏃 Initializing pose tracking...")
        if use_tasks:
            try:
                pose_processor = TasksPoseProcessor(
                    threaded_osc, 
                    show_fps=show_fps,  # Enable FPS for pose in all modes
                    config=config,
                    force_cpu=force_cpu,
                    force_gpu=force_gpu,
                    is_apple_silicon=IS_APPLE_SILICON
                )
                pose_landmarker, pose_backend, _, success = pose_processor.setup_processor()
                if success:
                    pose_is_tasks = True
                    backend_names.append(pose_backend)
                    print("✅ Using MediaPipe Tasks (Pose)")
                else:
                    pose_processor = None
            except Exception as e:
                print(f"⚠️  Tasks pose processor failed: {e}")
                pose_processor = None
        
        if pose_processor is None:
            try:
                pose_processor = LegacyPoseProcessor(
                    threaded_osc, 
                    show_fps=show_fps,  # Enable FPS for pose in all modes
                    config=config
                )
                pose_landmarker, pose_backend, _ = pose_processor.setup_processor()
                pose_is_tasks = False
                backend_names.append(pose_backend)
                print("✅ Using Legacy MediaPipe (Pose)")
            except Exception as e:
                print(f"❌ Legacy pose processor setup failed: {e}")
                if tracking_mode == 'pose':
                    print("🛑 Cannot initialize pose processing backend")
                    return 1
    
    # ------------------------------------------------------------------------
    # Setup Hand Processor (if mode is 'hand' or 'all')
    # ------------------------------------------------------------------------
    if tracking_mode in ['hand', 'all'] and not holistic_active:
        print("✋ Initializing hand tracking...")
        # Only enable FPS on hand if pose is not running (to avoid duplicate output)
        hand_show_fps = show_fps if tracking_mode == 'hand' else False
        if use_tasks:
            try:
                hand_processor = TasksHandProcessor(
                    threaded_osc, 
                    show_fps=hand_show_fps,
                    config=config,
                    force_cpu=force_cpu,
                    force_gpu=force_gpu,
                    is_apple_silicon=IS_APPLE_SILICON
                )
                hand_landmarker, hand_backend, _, success = hand_processor.setup_processor()
                if success:
                    hand_is_tasks = True
                    backend_names.append(hand_backend)
                    print("✅ Using MediaPipe Tasks (Hand)")
                else:
                    hand_processor = None
            except Exception as e:
                print(f"⚠️  Tasks hand processor failed: {e}")
                hand_processor = None
        
        if hand_processor is None:
            try:
                hand_processor = LegacyHandProcessor(
                    threaded_osc, 
                    show_fps=show_fps if tracking_mode == 'hand' else False,
                    config=config
                )
                hand_landmarker, hand_backend, _ = hand_processor.setup_processor()
                hand_is_tasks = False
                backend_names.append(hand_backend)
                print("✅ Using Legacy MediaPipe (Hand)")
            except Exception as e:
                print(f"❌ Legacy hand processor setup failed: {e}")
                if tracking_mode == 'hand':
                    print("🛑 Cannot initialize hand processing backend")
                    return 1
    
    # Verify at least one processor initialized for 'all' mode
    if tracking_mode == 'all' and pose_processor is None and hand_processor is None:
        print("🛑 Cannot initialize any processing backend")
        return 1

    # Guard against mixed-backend mode: the main loop below picks a single
    # loop style (Tasks vs. Legacy) for both processors, so if one processor
    # landed on Tasks and the other fell back to Legacy, whichever one isn't
    # on the chosen loop's backend never gets its process_frame() called -
    # its OSC output silently goes dark even though startup printed success
    # for it. Fail loudly instead of shipping a half-working session.
    if pose_processor is not None and hand_processor is not None and pose_is_tasks != hand_is_tasks:
        fallback_tracker = "hand" if pose_is_tasks else "pose"
        print(f"❌ Pose and hand landed on different backends (pose_is_tasks={pose_is_tasks}, hand_is_tasks={hand_is_tasks})")
        print(f"   The {fallback_tracker} tracker fell back to the Legacy MediaPipe API while the other stayed on Tasks")
        print("   These two backends can't currently run together in one processing loop")
        print("   --force-legacy forces both trackers onto the same (Legacy) backend, but it's")
        print("   deprecated and will be removed in 0.2.0 - treat this as a stopgap, not a fix")
        return 1
    
    # Set window title based on mode
    if tracking_mode == 'pose':
        window_title = "MediaPipe Pose Detection"
    elif tracking_mode == 'hand':
        window_title = "MediaPipe Hand Detection"
    else:
        window_title = "MediaPipe Pose + Hand Detection"
    
    # ------------------------------------------------------------------------
    # Configure display window
    # ------------------------------------------------------------------------
    if display_config.get('window_title'):
        window_title = display_config['window_title']
    
    backend_str = " + ".join(backend_names) if backend_names else "None"
    print(f"🚀 Mode: {tracking_mode.upper()}")
    print(f"🚀 Backend(s): {backend_str}")
    print(f"🖼️  Window: {window_title}")
    if display_config.get('mirror_preview', False):
        print("🪞 Preview mirrored (display only - OSC coordinates are unchanged)")
    if args.force_legacy:
        print("⚠️  --force-legacy is deprecated and will be removed in 0.2.0; "
              "the legacy MediaPipe Solutions API is being replaced by the unified Tasks-only pipeline")
    # Sentinel the launcher watches for to end its startup spinner
    print("🟢 Engine ready")

    # ========================================================================
    # MAIN PROCESSING LOOP
    # ========================================================================
    # Tracks *why* the loop ended so the final return code (below the
    # `finally:` cleanup) can tell a clean stop from capture loss from an
    # unhandled crash, instead of always reporting success.
    exit_reason = EXIT_OK
    try:
        # NDI may have gaps between frames - allow more failures
        consecutive_failures = 0
        try:
            # OpenCV raises if the capture never opened, so treat that as non-NDI
            is_ndi = cap.getBackendName() == "NDI"
        except Exception:
            is_ndi = False
        max_consecutive_failures = 100 if is_ndi else 30  # NDI: ~5s, Camera: ~1s

        if not cap.isOpened():
            print("❌ Video capture is not open - nothing to process")
            print("   Check the camera device ID or NDI source name")
            print("   On macOS, confirm camera access in System Settings > Privacy & Security > Camera")
            return 1
        
        # Determine if we're using Tasks API (all processors must use same mode for simplicity)
        # For 'all' mode, we process both sequentially on same frame
        use_tasks_loop = (pose_is_tasks if pose_processor else False) or (hand_is_tasks if hand_processor else False)
        
        if use_tasks_loop:
            # Tasks processing with async callback
            last_frame_time = time.time()
            mirror_preview = display_config.get('mirror_preview', False)
            
            while cap.isOpened():
                # Frame rate limiting - sleep to maintain target fps
                if frame_interval > 0:
                    current_time = time.time()
                    elapsed = current_time - last_frame_time
                    if elapsed < frame_interval:
                        sleep_time = frame_interval - elapsed
                        time.sleep(sleep_time)
                    last_frame_time = time.time()
                
                ret, frame = cap.read()
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        print(f"❌ Too many consecutive frame failures ({consecutive_failures})")
                        exit_reason = EXIT_CAPTURE_LOST
                        break
                    continue

                consecutive_failures = 0

                try:
                    timestamp_counter += 1

                    # Each processor's inference always reads the clean
                    # source `frame` directly, never a previous processor's
                    # annotated output - otherwise the second processor's
                    # model would see the first processor's skeleton painted
                    # over the pixels it's about to analyze, and would
                    # letterbox an already-letterboxed frame (an identity
                    # transform) instead of the true source frame, breaking
                    # its OSC coordinate mapping when processing/source
                    # aspect ratios differ. `display` accumulates both
                    # processors' overlays onto one shared array instead.
                    display = None
                    if pose_processor and pose_is_tasks:
                        display = pose_processor.process_frame(frame, pose_landmarker, "Pose", timestamp_counter, draw_target=display)

                    if hand_processor and hand_is_tasks:
                        display = hand_processor.process_frame(frame, hand_landmarker, "Hand", timestamp_counter, draw_target=display)

                    if display_config.get('show_window', True):
                        show_preview(display, window_title, mirror_preview)

                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            break
                        # Re-fight HighGUI for the Dock policy right after
                        # it has had its chance to reset it on window
                        # creation; reassert_accessory_policy() caps itself
                        # after a few calls, so this is cheap once it wins.
                        if reassert_dock_policy is not None:
                            reassert_dock_policy()
                        # The red close button destroys the window; without
                        # this check the loop would keep running and imshow
                        # would recreate it on the next frame.
                        try:
                            if cv2.getWindowProperty(window_title, cv2.WND_PROP_VISIBLE) < 1:
                                print("🛑 Preview window closed - stopping")
                                break
                        except cv2.error:
                            break

                except Exception as frame_error:
                    print(f"⚠️  Tasks frame processing error: {frame_error}")
                    continue
        else:
            # Legacy processing with context manager
            # Create context managers for active processors
            pose_ctx = pose_landmarker if pose_processor and not pose_is_tasks else None
            hand_ctx = hand_landmarker if hand_processor and not hand_is_tasks else None
            
            # Handle legacy context managers
            if pose_ctx and hand_ctx:
                with pose_ctx as pose, hand_ctx as hand:
                    exit_reason = _legacy_loop(cap, pose_processor, pose, hand_processor, hand,
                                display_config, window_title, max_consecutive_failures, show_fps, tracking_mode,
                                frame_interval, reassert_dock_policy)
            elif pose_ctx:
                with pose_ctx as pose:
                    exit_reason = _legacy_loop(cap, pose_processor, pose, None, None,
                                display_config, window_title, max_consecutive_failures, show_fps, tracking_mode,
                                frame_interval, reassert_dock_policy)
            elif hand_ctx:
                with hand_ctx as hand:
                    exit_reason = _legacy_loop(cap, None, None, hand_processor, hand,
                                display_config, window_title, max_consecutive_failures, show_fps, tracking_mode,
                                frame_interval, reassert_dock_policy)

    except KeyboardInterrupt:
        # This is how the launcher stops the engine cleanly (it sends SIGINT
        # first) - a normal Stop must keep exiting 0, not look like a failure.
        print("\n🛑 Interrupted by user")
        exit_reason = EXIT_OK
    except Exception as main_error:
        print(f"❌ Main processing error: {main_error}")
        print("🛑 Application will exit")
        exit_reason = EXIT_CRASH

    # ========================================================================
    # CLEANUP
    # ========================================================================
    finally:
        # Release MediaPipe landmarkers (Tasks API holds native graph resources)
        for landmarker in (pose_landmarker, hand_landmarker):
            try:
                if landmarker is not None and hasattr(landmarker, 'close'):
                    landmarker.close()
            except:
                pass
        try:
            threaded_osc.stop()
        except:
            pass
        try:
            cap.release()
        except:
            pass
        try:
            if display_config.get('show_window', True):
                cv2.destroyAllWindows()
        except:
            pass
        print("✅ Cleanup completed")

    # Read after `finally:` runs, not used to skip cleanup - whatever ended
    # the loop (clean stop, capture loss, or an unhandled crash) determines
    # the process exit code the launcher sees.
    return exit_reason


# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================
def main(argv=None):
    """
    Parse arguments, load configuration, and start tracking

    Args:
        argv: Argument list to parse (defaults to sys.argv[1:])

    Returns:
        Process exit code (0 on success)
    """
    args = parse_args(argv)

    # When the launcher spawns us, drop out of the Dock before any window
    # exists so the engine doesn't get a second identical Dock tile.
    if os.environ.get('MPOSC_LAUNCHED_FROM_GUI'):
        from src.macos_app import set_accessory_policy
        set_accessory_policy()

    config = get_config()
    apply_config_overrides(args, config)

    if handle_utility_commands(args, config):
        return 0

    print_platform_info()

    return run(args, config)


if __name__ == "__main__":
    sys.exit(main() or 0)
