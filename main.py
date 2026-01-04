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
import platform
import time
from pythonosc import udp_client

# Import modular components
from src import ThreadedOSCSender, TasksPoseProcessor, LegacyPoseProcessor, get_config
from src import NDICapture, list_ndi_sources, NDI_AVAILABLE


# ============================================================================
# PLATFORM DETECTION
# ============================================================================
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"


# ============================================================================
# COMMAND LINE ARGUMENT PARSING
# ============================================================================
parser = argparse.ArgumentParser(description='MediaPipe Pose Detection with OSC')
parser.add_argument('--fps', action='store_true', help='Show FPS counter (overrides config)')
parser.add_argument('--config', default='config.json', help='Configuration file path (default: config.json)')
parser.add_argument('--create-config', action='store_true', help='Create default configuration file and exit')
parser.add_argument('--show-config', action='store_true', help='Show current configuration and exit')
parser.add_argument('--host', help='OSC host address (overrides config)')
parser.add_argument('--port', type=int, help='OSC port (overrides config)')
parser.add_argument('--camera', type=int, help='Camera device ID (overrides config)')
parser.add_argument('--force-cpu', action='store_true', help='Force CPU delegate (skip GPU)')
parser.add_argument('--force-legacy', action='store_true', help='Force Legacy MediaPipe (skip Tasks API)')
parser.add_argument('--ndi', action='store_true', help='Use NDI input instead of camera')
parser.add_argument('--ndi-source', type=str, help='NDI source name to connect to')
parser.add_argument('--list-ndi', action='store_true', help='List available NDI sources and exit')
args = parser.parse_args()


# ============================================================================
# CONFIGURATION LOADING AND OVERRIDES
# ============================================================================
config = get_config()
if args.config != 'config.json':
    config.config_file = args.config
    config.config = config._load_config()

# Apply command line argument overrides
if args.fps:
    config.set('performance', 'show_fps', True)
if args.host:
    config.set('osc', 'host', args.host)
if args.port:
    config.set('osc', 'port', args.port)
if args.camera:
    config.set('camera', 'device_id', args.camera)


# ============================================================================
# HANDLE UTILITY COMMANDS (exit after execution)
# ============================================================================
if args.create_config:
    config.create_default_config_file()
    exit(0)

if args.show_config:
    config.print_config()
    exit(0)

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
    exit(0)


# ============================================================================
# PLATFORM AND GPU INFORMATION
# ============================================================================
print(f"🖥️  Platform: {platform.system()} {platform.machine()}")
if IS_APPLE_SILICON:
    print("🍎 Apple Silicon detected - using SRGBA format for GPU compatibility")

# Check for TensorFlow GPU support (optional, for informational purposes)
try:
    import tensorflow as tf
    gpu_available = len(tf.config.experimental.list_physical_devices('GPU')) > 0
    print(f"TensorFlow GPU available: {gpu_available}")
except ImportError:
    print("TensorFlow not available - MediaPipe will use its own GPU/CPU detection")


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
        cv2.VideoCapture or NDICapture object
    """
    camera_config = config.get('camera')
    
    # Determine if NDI should be used (command line or config)
    use_ndi = use_ndi or camera_config.get('use_ndi', False)
    ndi_source = ndi_source or camera_config.get('ndi_source')
    
    # ------------------------------------------------------------------------
    # Try NDI capture if requested
    # ------------------------------------------------------------------------
    if use_ndi:
        if not NDI_AVAILABLE:
            print("❌ NDI requested but ndi-python not installed")
            print("   Install with: uv add ndi-python")
            print("   Falling back to camera...")
        else:
            print("🎬 Setting up NDI capture...")
            try:
                cap = NDICapture(source_name=ndi_source)
                if cap.isOpened():
                    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    proc_w = camera_config.get('processing_width', 640)
                    proc_h = camera_config.get('processing_height', 480)
                    if actual_w != proc_w or actual_h != proc_h:
                        print(f"📐 NDI: {actual_w}x{actual_h} → processing at {proc_w}x{proc_h}")
                    return cap
                else:
                    print("❌ NDI capture failed to open, falling back to camera...")
            except Exception as e:
                print(f"❌ NDI setup failed: {e}")
                print("   Falling back to camera...")
    
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
# MAIN APPLICATION FUNCTION
# ============================================================================
def main():
    """
    Main application loop
    Initializes OSC, camera, and pose processor, then runs processing loop
    """
    # ------------------------------------------------------------------------
    # Get configuration sections
    # ------------------------------------------------------------------------
    osc_config = config.get('osc')
    performance_config = config.get('performance')
    display_config = config.get('display')
    
    # ------------------------------------------------------------------------
    # Initialize OSC communication
    # ------------------------------------------------------------------------
    print(f"🌐 OSC Target: {osc_config['host']}:{osc_config['port']}")
    osc_client = udp_client.SimpleUDPClient(osc_config['host'], osc_config['port'])
    threaded_osc = ThreadedOSCSender(osc_client, queue_size=osc_config['queue_size'])
    
    # ------------------------------------------------------------------------
    # Setup camera or NDI capture
    # ------------------------------------------------------------------------
    cap = setup_camera(config, use_ndi=args.ndi, ndi_source=args.ndi_source)
    
    # ------------------------------------------------------------------------
    # Initialize pose processor (Tasks API with GPU/CPU fallback)
    # ------------------------------------------------------------------------
    show_fps = performance_config['show_fps']
    
    # Try MediaPipe Tasks first (preferred), fallback to Legacy
    processor = None
    landmarker = None
    backend_name = None
    window_title = None
    is_tasks = False
    timestamp_counter = 0
    
    # Determine processing strategy
    use_tasks = not args.force_legacy
    force_cpu = args.force_cpu
    
    # Try Tasks processor first (unless forced to legacy)
    if use_tasks:
        try:
            processor = TasksPoseProcessor(
                threaded_osc, 
                show_fps=show_fps, 
                config=config,
                force_cpu=force_cpu,
                is_apple_silicon=IS_APPLE_SILICON
            )
            landmarker, backend_name, window_title, success = processor.setup_processor()
            if success:
                is_tasks = True
                print("✅ Using MediaPipe Tasks")
            else:
                processor = None
        except Exception as e:
            print(f"⚠️  Tasks processor failed: {e}")
            processor = None
    
    # ------------------------------------------------------------------------
    # Fallback to Legacy processor if Tasks failed or was skipped
    # ------------------------------------------------------------------------
    if processor is None:
        try:
            processor = LegacyPoseProcessor(threaded_osc, show_fps=show_fps, config=config)
            landmarker, backend_name, window_title = processor.setup_processor()
            is_tasks = False
            print("✅ Using Legacy MediaPipe")
        except Exception as e:
            print(f"❌ Legacy processor setup failed: {e}")
            print("🛑 Cannot initialize any processing backend")
            return
    
    # ------------------------------------------------------------------------
    # Configure display window
    # ------------------------------------------------------------------------
    if display_config.get('window_title'):
        window_title = display_config['window_title']
    
    print(f"🚀 Backend: {backend_name}")
    print(f"🖼️  Window: {window_title}")
    
    # ========================================================================
    # MAIN PROCESSING LOOP
    # ========================================================================
    try:
        # NDI may have gaps between frames - allow more failures
        consecutive_failures = 0
        is_ndi = hasattr(cap, 'getBackendName') and cap.getBackendName() == "NDI"
        max_consecutive_failures = 100 if is_ndi else 30  # NDI: ~5s, Camera: ~1s
        
        if is_tasks:
            # Tasks processing with async callback
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        print(f"❌ Too many consecutive frame failures ({consecutive_failures})")
                        break
                    continue  # Skip this frame, try again
                
                consecutive_failures = 0  # Reset on successful read
                
                try:
                    timestamp_counter += 1
                    image = processor.process_frame(frame, landmarker, backend_name, timestamp_counter)
                    
                    if display_config['show_window']:
                        cv2.imshow(window_title, image)
                    
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                        
                except Exception as frame_error:
                    print(f"⚠️  Tasks frame processing error: {frame_error}")
                    continue
        else:
            # Legacy processing with context manager
            with landmarker as pose:
                while cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            print(f"❌ Too many consecutive frame failures ({consecutive_failures})")
                            break
                        continue  # Skip this frame, try again
                    
                    consecutive_failures = 0  # Reset on successful read
                    
                    try:
                        image = processor.process_frame(frame, pose, backend_name)
                        
                        if display_config['show_window']:
                            cv2.imshow(window_title, image)
                        
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            break
                    except Exception as frame_error:
                        print(f"⚠️  Legacy frame processing error: {frame_error}")
                        continue
    
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
    except Exception as main_error:
        print(f"❌ Main processing error: {main_error}")
        print("🛑 Application will exit")
    
    # ========================================================================
    # CLEANUP
    # ========================================================================
    finally:
        try:
            threaded_osc.stop()
        except:
            pass
        try:
            cap.release()
        except:
            pass
        try:
            if display_config['show_window']:
                cv2.destroyAllWindows()
        except:
            pass
        print("✅ Cleanup completed")


# ============================================================================
# ENTRY POINT
# ============================================================================
if __name__ == "__main__":
    main()
