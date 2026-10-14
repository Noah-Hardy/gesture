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
import gc
import platform
import sys
import time
from pythonosc import udp_client

# Import modular components
from src import ThreadedOSCSender, TasksPoseProcessor, get_config
from src import TasksHandProcessor
from src import TasksHolisticProcessor
from src import OscEmitter
from src.config import DEFAULT_OSC_PROTOCOL, OSC_PROTOCOLS
from src import NDICapture, list_ndi_sources, NDI_AVAILABLE
from src.camera_permission import camera_permission_denied
from src.engine_channel import EngineChannel, NullChannel, exit_on_launcher_loss
from src.ipc_protocol import IPC_FD_FLAG, IPC_SHM_FLAG
from src.live_settings import LiveSettings
from src.runtime import (FrameClock, ReconnectingCapture, install_sigterm_handler,
                         report_phase, set_phase_handler)


# ============================================================================
# PLATFORM DETECTION
# ============================================================================
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"


# ============================================================================
# EXIT CODES
# ============================================================================
# Setup failures that happen before the processing loop even starts (camera
# open failure, no processor could be constructed, bad
# OSC target, ...) keep using the bare `1` they already used - that's out of
# scope here. These are specifically for how run() reports *why the
# processing loop itself ended*, so the launcher can tell a clean stop from
# a real failure instead of treating every nonzero code the same way.
EXIT_OK = 0                # Clean stop: user Stop (SIGINT), 'q', window closed
EXIT_CAPTURE_LOST = 2      # Capture stayed lost past camera.reconnect_timeout
EXIT_CRASH = 3             # Unhandled exception inside the processing loop


# ============================================================================
# PREVIEW WINDOW TITLE
# ============================================================================
# Title of the --preview-window cv2 window (CLI use only; under the launcher
# the preview is drawn in-app from the shared frame buffer, #65). Kept
# unambiguous rather than mode-specific so it never regresses to a name that
# sounds like the OSC output (see issue #60).
PREVIEW_WINDOW_TITLE = "Gesture Preview — not the OSC output"


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
    parser.add_argument('--osc-protocol', choices=OSC_PROTOCOLS, default=None,
                        help='OSC output format: float (native floats per landmark, bundled; the default), json (JSON v2, bundled) or legacy (0.2.x JSON, deprecated). Overrides config')
    parser.add_argument('--camera', type=int, help='Camera device ID (overrides config)')
    parser.add_argument('--force-cpu', action='store_true', help='Force CPU delegate (skip GPU)')
    parser.add_argument('--force-gpu', action='store_true', help='Force GPU delegate (WARNING: has memory leak on Apple Silicon)')
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
    parser.add_argument('--preview-window', '--preview', dest='preview_window', action='store_true',
                        help='Show a local OpenCV preview window (CLI use; local confirmation only, not the OSC output). Off by default - the launcher draws the preview in-app')
    # 0.3 spelling, kept so existing scripts and the Tk launcher still parse; headless is now the default
    parser.add_argument('--no-preview', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument(IPC_FD_FLAG, type=int, default=None, metavar='FD',
                        help='Launcher IPC: inherited socket file descriptor for status, control and exit detection (set by the launcher)')
    parser.add_argument(IPC_SHM_FLAG, default=None, metavar='NAME',
                        help='Launcher IPC: name of the shared-memory segment the engine creates for preview frames (set by the launcher)')
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
    if args.osc_protocol:
        config.set('osc', 'protocol', args.osc_protocol)
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
def setup_camera(config, use_ndi=False, ndi_source=None, warmup=True):
    """
    Initialize video capture from camera or NDI source
    
    Args:
        config: Configuration object with camera settings
        use_ndi: Boolean to use NDI instead of camera
        ndi_source: Name of NDI source to connect to
        warmup: Wait up to 3s for the webcam's first frame. Reopens during
            a reconnect skip it so the processing loop keeps iterating -
            ReconnectingCapture's backoff covers the slow start instead
        
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
            cap = NDICapture(source_name=ndi_source,
                             bandwidth=camera_config.get('ndi_bandwidth', 'lowest'))
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
    
    if not warmup:
        return cap

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


def reopen_capture(config, old_cap, use_ndi=False, ndi_source=None):
    """
    Replace a capture that stopped delivering frames (ReconnectingCapture's
    reopen hook, #32)

    Args:
        config: Configuration object with camera settings
        old_cap: The failing capture (may be None after a failed reopen)
        use_ndi, ndi_source: The same values the session was opened with,
            so an NDI session never falls back to the webcam

    Returns:
        The capture to read from next - may be None, which the wrapper
        treats as one more failed read and retries after its backoff
    """
    # NDI: rebuild the receiver through the retained finder instead of
    # re-initializing NDI and waiting out a full discovery (#31)
    if hasattr(old_cap, 'reconnect'):
        old_cap.reconnect()
        return old_cap
    if old_cap is not None:
        try:
            old_cap.release()
        except Exception:
            pass
    return setup_camera(config, use_ndi=use_ndi, ndi_source=ndi_source, warmup=False)


# ============================================================================
# CAMERA FAULT REPORTING
# ============================================================================
CAMERA_DENIED_REASON = "Camera access is off for Gesture - allow it in System Settings > Privacy & Security > Camera"


def report_camera_unavailable(config):
    """
    Report why a webcam could not be opened: macOS blocking access
    (permission_denied) or anything else (no_camera)

    Args:
        config: Configuration object (for the device id in the message)
    """
    if camera_permission_denied():
        print("❌ Camera access is denied")
        print("   Allow it in System Settings > Privacy & Security > Camera, then start again")
        report_phase('permission_denied', reason=CAMERA_DENIED_REASON)
    else:
        device_id = config.get('camera', 'device_id', 0)
        print(f"❌ Camera {device_id} could not be opened")
        print("   Check the camera device ID, and that no other app is using the camera")
        report_phase('no_camera', reason=f"Camera {device_id} could not be opened")


def report_capture_state(config, using_ndi, state, reason):
    """
    Map a ReconnectingCapture state change onto the status channel's phases

    Args:
        config: Configuration object
        using_ndi: The session reads an NDI source (no permission check then)
        state: 'reconnecting', 'recovered' or 'gave_up'
        reason: Detail from the capture wrapper
    """
    if state == 'reconnecting':
        report_phase('reconnecting', reason=reason)
    elif state == 'recovered':
        report_phase('ready')
    elif state == 'gave_up':
        if not using_ndi and camera_permission_denied():
            report_phase('permission_denied', reason=CAMERA_DENIED_REASON)
        else:
            report_phase('no_camera', reason=reason)


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
# MAIN APPLICATION FUNCTION
# ============================================================================
def run(args, config, channel=None):
    """
    Main application loop
    Initializes OSC, camera, and pose processor, then runs processing loop

    Args:
        args: Parsed command line arguments
        config: Configuration object
        channel: EngineChannel to the launcher (status, control, preview
            frames), or None when running standalone

    Returns:
        Process exit code (0 on success)
    """
    # ------------------------------------------------------------------------
    # Get configuration sections
    # ------------------------------------------------------------------------
    osc_config = config.get('osc')
    performance_config = config.get('performance')

    if channel is None:
        channel = NullChannel()
    preview_window = bool(getattr(args, 'preview_window', False))

    # ------------------------------------------------------------------------
    # Initialize OSC communication
    # ------------------------------------------------------------------------
    print(f"🌐 OSC Target: {osc_config['host']}:{osc_config['port']}")
    try:
        # allow_broadcast: a subnet broadcast target (e.g. x.x.x.255) otherwise
        # fails every send with PermissionError (#55)
        osc_client = udp_client.SimpleUDPClient(osc_config['host'], osc_config['port'], allow_broadcast=True)
        threaded_osc = ThreadedOSCSender(osc_client, queue_size=osc_config['queue_size'])
    except OSError as e:
        print(f"❌ Could not resolve OSC target {osc_config['host']}:{osc_config['port']}: {e}")
        print("   Check the OSC host address for typos")
        print("   If using a hostname, try the IP address instead")
        return 1
    # Every processor sends through the emitter, which owns the wire format
    osc_emitter = OscEmitter(threaded_osc, osc_config.get('protocol', DEFAULT_OSC_PROTOCOL), config)
    print(f"📡 OSC protocol: {osc_emitter.protocol}")
    
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
    using_ndi = args.ndi or config.get('camera', 'use_ndi', False)
    report_phase('opening_camera',
                 message="Connecting to NDI source…" if using_ndi else "Opening camera…")
    cap = setup_camera(config, use_ndi=args.ndi, ndi_source=args.ndi_source)
    if cap is None:
        # setup_camera already printed a specific ❌ error (NDI was requested
        # and definitively failed) - do not fall back to the webcam.
        report_phase('no_camera', reason="NDI source unavailable")
        return 1
    if not using_ndi and not cap.isOpened():
        report_camera_unavailable(config)
        return 1

    # ------------------------------------------------------------------------
    # Initialize processor(s) based on mode (pose/hand/all)
    # ------------------------------------------------------------------------
    show_fps = performance_config['show_fps']
    tracking_mode = args.mode

    force_cpu = args.force_cpu
    force_gpu = args.force_gpu
    timestamp_counter = 0

    # Processor containers
    pose_processor = None
    pose_landmarker = None

    hand_processor = None
    hand_landmarker = None

    backend_names = []

    # ------------------------------------------------------------------------
    # Setup Holistic Processor (preferred for 'all' mode - pose + hands in
    # a single model pass instead of two landmarkers per frame)
    # ------------------------------------------------------------------------
    holistic_active = False
    if tracking_mode == 'all' and not args.no_holistic:
        num_poses = config.get('mediapipe', 'num_poses', 1)
        if num_poses > 1:
            print("⚠️  Holistic landmarker is single-person (num_poses > 1 configured)")
            print("   Falling back to separate pose + hand landmarkers")
        else:
            print("🧍 Initializing holistic tracking (pose + hands, one model)...")
            try:
                holistic_processor = TasksHolisticProcessor(
                    osc_emitter,
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
        try:
            pose_processor = TasksPoseProcessor(
                osc_emitter,
                show_fps=show_fps,  # Enable FPS for pose in all modes
                config=config,
                force_cpu=force_cpu,
                force_gpu=force_gpu,
                is_apple_silicon=IS_APPLE_SILICON
            )
            pose_landmarker, pose_backend, _, success = pose_processor.setup_processor()
            if success:
                backend_names.append(pose_backend)
                print("✅ Using MediaPipe Tasks (Pose)")
            else:
                pose_processor = None
        except Exception as e:
            print(f"⚠️  Tasks pose processor failed: {e}")
            pose_processor = None

        if pose_processor is None and tracking_mode == 'pose':
            print("🛑 Cannot initialize pose processing backend")
            return 1

    # ------------------------------------------------------------------------
    # Setup Hand Processor (if mode is 'hand' or 'all')
    # ------------------------------------------------------------------------
    if tracking_mode in ['hand', 'all'] and not holistic_active:
        print("✋ Initializing hand tracking...")
        # Only enable FPS on hand if pose is not running (to avoid duplicate output)
        hand_show_fps = show_fps if tracking_mode == 'hand' else False
        try:
            hand_processor = TasksHandProcessor(
                osc_emitter,
                show_fps=hand_show_fps,
                config=config,
                force_cpu=force_cpu,
                force_gpu=force_gpu,
                is_apple_silicon=IS_APPLE_SILICON
            )
            hand_landmarker, hand_backend, _, success = hand_processor.setup_processor()
            if success:
                backend_names.append(hand_backend)
                print("✅ Using MediaPipe Tasks (Hand)")
            else:
                hand_processor = None
        except Exception as e:
            print(f"⚠️  Tasks hand processor failed: {e}")
            hand_processor = None

        if hand_processor is None and tracking_mode == 'hand':
            print("🛑 Cannot initialize hand processing backend")
            return 1

    # Verify at least one processor initialized for 'all' mode
    if tracking_mode == 'all' and pose_processor is None and hand_processor is None:
        print("🛑 Cannot initialize any processing backend")
        return 1

    backend_str = " + ".join(backend_names) if backend_names else "None"
    print(f"🚀 Mode: {tracking_mode.upper()}")
    print(f"🚀 Backend(s): {backend_str}")
    if preview_window:
        print(f"🖼️  Window: {PREVIEW_WINDOW_TITLE}")
    if config.get('display', 'mirror_preview', False):
        print("🪞 Preview mirrored (display only - OSC coordinates are unchanged)")

    # In all mode without holistic, pose and hand read the same frame -
    # letterbox and colour-convert it once for both (#36)
    if pose_processor is not None and hand_processor is not None:
        hand_processor.share_frame_prep(pose_processor)

    # Run each landmarker once on a blank frame so the first real frame is
    # not delayed by lazy graph initialization. A failure here is not fatal:
    # the first real frame just pays the cost instead.
    report_phase('warming_up')
    for processor, landmarker in ((pose_processor, pose_landmarker), (hand_processor, hand_landmarker)):
        if processor is not None:
            try:
                processor.warm_up(landmarker)
            except Exception as e:
                print(f"⚠️  Warm-up failed: {e}")

    # Setup is done: freeze everything it allocated (models, modules,
    # config) into the permanent generation so no collection ever rescans
    # it. That keeps the collector's periodic passes cheap without the old
    # forced gc.collect() every N frames, which stalled the capture thread
    # for milliseconds at a time - frame jitter receivers see (#36).
    gc.collect()
    gc.freeze()
    if not performance_config.get('gc_enabled', True):
        gc.disable()

    # The launcher's ready signal is this phase on the status channel
    report_phase('ready')

    # ========================================================================
    # MAIN PROCESSING LOOP
    # ========================================================================
    # Tracks *why* the loop ended so the final return code (below the
    # `finally:` cleanup) can tell a clean stop from capture loss from an
    # unhandled crash, instead of always reporting success.
    exit_reason = EXIT_OK
    live = None
    try:
        # Failed reads back off and reopen instead of busy-spinning, and
        # only a loss lasting camera.reconnect_timeout seconds ends the
        # session (#32). Rebinding `cap` means the cleanup below releases
        # whichever capture is current after any reopen.
        cap = ReconnectingCapture(
            cap,
            reopen=lambda old_cap: reopen_capture(config, old_cap, args.ndi, args.ndi_source),
            timeout=config.get('camera', 'reconnect_timeout', 30),
            on_state=lambda state, reason: report_capture_state(config, using_ndi, state, reason),
        )

        if not cap.isOpened():
            print("❌ Video capture is not open - nothing to process")
            print("   Check the camera device ID or NDI source name")
            print("   On macOS, confirm camera access in System Settings > Privacy & Security > Camera")
            report_phase('no_camera', reason="Video capture is not open")
            return 1

        # Tasks processing with async callback
        frame_clock = FrameClock(frame_interval)
        live = LiveSettings(config, osc_emitter, threaded_osc, frame_clock,
                            pose_processor, hand_processor, pose_landmarker, hand_landmarker,
                            channel, preview_window=preview_window)

        # Not `while cap.isOpened()` - during a reconnect the capture can be
        # closed for a while, and the loop has to keep iterating through it
        while True:
            # The launcher asked to stop, or closed its end of the socket
            # (quit, crash, force-quit): don't keep holding the camera
            if channel.should_stop:
                if channel.closed.is_set():
                    print("🛑 Launcher is gone - stopping")
                else:
                    print("🛑 Stop requested by the launcher")
                break

            # 1 Hz heartbeat first, so it keeps going through frame-read failures
            osc_emitter.heartbeat()

            # Live `set` commands, applied between frames (may rebuild the landmarkers)
            live.apply_pending()
            live.update_draw_state()
            channel.tick(osc_emitter)

            # Frame rate limiting (deadline-based - see FrameClock)
            frame_clock.wait()

            # A failed read returns immediately (after at most a short backoff
            # slice) so this loop - and anything at its top - keeps running
            # while the capture reconnects
            ret, frame = cap.read()
            if not ret:
                if cap.gave_up:
                    exit_reason = EXIT_CAPTURE_LOST
                    break
                continue
            frame_read_at = time.perf_counter()

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
                osc_emitter.begin_frame(time.time())
                if pose_processor:
                    display = pose_processor.process_frame(frame, live.pose_landmarker, "Pose", timestamp_counter, draw_target=display)

                if hand_processor:
                    display = hand_processor.process_frame(frame, live.hand_landmarker, "Hand", timestamp_counter, draw_target=display)
                osc_emitter.end_frame()
                # Frame read -> packets queued; what the status channel calls latency
                channel.frame_done((time.perf_counter() - frame_read_at) * 1000.0)

                # Preview: the launcher's shared buffer, and/or the CLI window.
                # The mirror flip is display-only - OSC stays in source space.
                if live.publish_frames:
                    channel.publish_frame(display, live.mirror)

                if preview_window:
                    show_preview(display, PREVIEW_WINDOW_TITLE, live.mirror)

                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                    # The red close button destroys the window; without
                    # this check the loop would keep running and imshow
                    # would recreate it on the next frame.
                    try:
                        if cv2.getWindowProperty(PREVIEW_WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
                            print("🛑 Preview window closed - stopping")
                            break
                    except cv2.error:
                        break

            except Exception as frame_error:
                print(f"⚠️  Frame processing error: {frame_error}")
                continue

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
        # The live settings own the landmarkers once a threshold change has
        # rebuilt them
        if live is not None:
            pose_landmarker, hand_landmarker = live.pose_landmarker, live.hand_landmarker
        # Release MediaPipe landmarkers (Tasks API holds native graph resources)
        for landmarker in (pose_landmarker, hand_landmarker):
            try:
                if landmarker is not None and hasattr(landmarker, 'close'):
                    landmarker.close()
            except:
                pass
        # Clear every channel on the receiver (#55), then let the sender
        # drain so those clears actually leave before the process exits
        try:
            osc_emitter.clear_all()
        except Exception:
            pass
        try:
            threaded_osc.stop(flush=True)
        except:
            pass
        try:
            cap.release()
        except:
            pass
        try:
            if preview_window:
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
def open_channel(args, config):
    """
    Adopt the launcher's IPC socket, if it passed one

    Args:
        args: Parsed command line arguments
        config: Configuration object (for the preview frame size)

    Returns:
        EngineChannel, or a no-op NullChannel when running standalone
    """
    if args.ipc_fd is None:
        return NullChannel()
    camera = config.get('camera')
    frame_size = (camera.get('processing_width', 640), camera.get('processing_height', 480))
    # Socket closed = launcher gone: interrupt the loop, with a watchdog for
    # when the main thread is stuck in native code
    holder = {}
    channel = EngineChannel.from_fd(args.ipc_fd, shm_name=args.ipc_shm, frame_size=frame_size,
                                    on_close=lambda: holder['exit']() if 'exit' in holder else None)
    holder['exit'] = exit_on_launcher_loss(channel)
    return channel


def main(argv=None):
    """
    Parse arguments, load configuration, and start tracking

    Args:
        argv: Argument list to parse (defaults to sys.argv[1:])

    Returns:
        Process exit code (0 on success)
    """
    args = parse_args(argv)

    # SIGTERM (the launcher's escalation, or a plain `kill`) unwinds through
    # run()'s cleanup like a SIGINT Stop instead of dying mid-frame
    install_sigterm_handler()

    config = get_config()
    apply_config_overrides(args, config)

    if handle_utility_commands(args, config):
        return 0

    print_platform_info()

    channel = open_channel(args, config)
    set_phase_handler(channel.on_phase)
    code = EXIT_OK
    reason = ""
    try:
        code = run(args, config, channel)
        reason = {EXIT_CAPTURE_LOST: "capture_lost", EXIT_CRASH: "crash", 1: "startup_failed"}.get(code, "")
    except KeyboardInterrupt:
        # A stop signal that landed before run()'s own handler was in place
        code = EXIT_OK
    finally:
        if channel.closed.is_set():
            reason = "launcher_gone"
        set_phase_handler(None)
        channel.close(reason)
    return code


if __name__ == "__main__":
    sys.exit(main() or 0)
