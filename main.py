import cv2
import argparse
import platform
from pythonosc import udp_client

# Import our modular components
from src import ThreadedOSCSender, TasksPoseProcessor, LegacyPoseProcessor, get_config

# Detect Apple Silicon
IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"

# Parse command line arguments
parser = argparse.ArgumentParser(description='Optimized MediaPipe Pose Detection with OSC')
parser.add_argument('--fps', action='store_true', help='Show FPS counter (overrides config)')
parser.add_argument('--config', default='config.json', help='Configuration file path (default: config.json)')
parser.add_argument('--create-config', action='store_true', help='Create default configuration file and exit')
parser.add_argument('--show-config', action='store_true', help='Show current configuration and exit')
parser.add_argument('--host', help='OSC host address (overrides config)')
parser.add_argument('--port', type=int, help='OSC port (overrides config)')
parser.add_argument('--camera', type=int, help='Camera device ID (overrides config)')
parser.add_argument('--force-cpu', action='store_true', help='Force CPU delegate (skip GPU)')
parser.add_argument('--force-legacy', action='store_true', help='Force Legacy MediaPipe (skip Tasks API)')
args = parser.parse_args()

# Load configuration
config = get_config()
if args.config != 'config.json':
    config.config_file = args.config
    config.config = config._load_config()

# Apply command line overrides
if args.fps:
    config.set('performance', 'show_fps', True)
if args.host:
    config.set('osc', 'host', args.host)
if args.port:
    config.set('osc', 'port', args.port)
if args.camera:
    config.set('camera', 'device_id', args.camera)

# Handle configuration commands
if args.create_config:
    config.create_default_config_file()
    exit(0)

if args.show_config:
    config.print_config()
    exit(0)

# Platform information
print(f"🖥️  Platform: {platform.system()} {platform.machine()}")
if IS_APPLE_SILICON:
    print("🍎 Apple Silicon detected - using SRGBA format for GPU compatibility")

# Try to import TensorFlow for GPU support info (optional)
try:
    import tensorflow as tf
    gpu_available = len(tf.config.experimental.list_physical_devices('GPU')) > 0
    print(f"TensorFlow GPU available: {gpu_available}")
except ImportError:
    gpu_available = False
    print("TensorFlow not available - MediaPipe will use its own GPU/CPU detection")


def setup_camera(config):
    """Setup camera with configuration settings"""
    camera_config = config.get('camera')
    
    cap = cv2.VideoCapture(camera_config['device_id'])
    cap.set(cv2.CAP_PROP_FPS, camera_config['fps'])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, camera_config['buffer_size'])
    
    # Set resolution
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, camera_config['width'])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_config['height'])
    
    print(f"📷 Camera setup: Device {camera_config['device_id']}, {camera_config['width']}x{camera_config['height']} @ {camera_config['fps']}fps")
    
    return cap


def main():
    """Main application loop"""
    # Get configuration sections
    osc_config = config.get('osc')
    performance_config = config.get('performance')
    display_config = config.get('display')
    
    # Create OSC client and threaded sender
    print(f"🌐 OSC Target: {osc_config['host']}:{osc_config['port']}")
    osc_client = udp_client.SimpleUDPClient(osc_config['host'], osc_config['port'])
    threaded_osc = ThreadedOSCSender(osc_client, queue_size=osc_config['queue_size'])
    
    # Setup camera
    cap = setup_camera(config)
    
    # Create pose processor with configuration
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
    
    # Fallback to Legacy processor if Tasks failed or was skipped
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
    
    # Override window title from config if specified
    if display_config.get('window_title'):
        window_title = display_config['window_title']
    
    print(f"🚀 Backend: {backend_name}")
    print(f"🖼️  Window: {window_title}")
    
    try:
        # Main processing loop
        if is_tasks:
            # Tasks processing with async callback
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                
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
                        break
                    
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
    
    finally:
        # Clean up
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


if __name__ == "__main__":
    main()
