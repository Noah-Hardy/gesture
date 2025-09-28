import cv2
import argparse
from pythonosc import udp_client

# Import our modular components
from src import ThreadedOSCSender, GPUPoseProcessor, CPUPoseProcessor, get_config

# Parse command line arguments
parser = argparse.ArgumentParser(description='Optimized MediaPipe Pose Detection with OSC')
parser.add_argument('--fps', action='store_true', help='Show FPS counter (overrides config)')
parser.add_argument('--config', default='config.json', help='Configuration file path (default: config.json)')
parser.add_argument('--create-config', action='store_true', help='Create default configuration file and exit')
parser.add_argument('--show-config', action='store_true', help='Show current configuration and exit')
parser.add_argument('--host', help='OSC host address (overrides config)')
parser.add_argument('--port', type=int, help='OSC port (overrides config)')
parser.add_argument('--camera', type=int, help='Camera device ID (overrides config)')
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

# Try to import TensorFlow for GPU support
try:
    import tensorflow as tf
    gpu_available = len(tf.config.experimental.list_physical_devices('GPU')) > 0
    print(f"TensorFlow GPU available: {gpu_available}")
except ImportError:
    gpu_available = False
    print("TensorFlow not available - using CPU only")


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
    use_gpu = False
    pose_landmarker = None
    pose_context = None
    
    # Try to setup GPU processing if preferred
    if performance_config['prefer_gpu']:
        try:
            processor = GPUPoseProcessor(threaded_osc, show_fps=show_fps, config=config)
            pose_landmarker, backend_name, window_title, use_gpu = processor.setup_gpu_processor()
            
            if not use_gpu:
                print("🔄 GPU setup failed, falling back to CPU processing")
        except Exception as e:
            print(f"⚠️  GPU processor creation failed: {e}")
            print("🔄 Falling back to CPU processing")
            use_gpu = False
    
    if not use_gpu:
        # Fallback to CPU processing
        try:
            processor = CPUPoseProcessor(threaded_osc, show_fps=show_fps, config=config)
            pose_context, backend_name, window_title = processor.setup_cpu_processor()
        except Exception as e:
            print(f"❌ CPU processor setup failed: {e}")
            print("🛑 Cannot initialize any processing backend")
            return
    
    # Override window title from config if specified
    if display_config.get('window_title'):
        window_title = display_config['window_title']
    
    print(f"🚀 Backend: {backend_name}")
    print(f"🖼️  Window: {window_title}")
    
    try:
        if use_gpu:
            # GPU processing loop with fallback
            try:
                while cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        break
                    
                    try:
                        image = processor.process_frame(frame, pose_landmarker, backend_name)
                        
                        if display_config['show_window']:
                            cv2.imshow(window_title, image)
                        
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            break
                    except Exception as frame_error:
                        print(f"⚠️  GPU frame processing error: {frame_error}")
                        print("🔄 Attempting to fall back to CPU processing...")
                        use_gpu = False
                        break
            except Exception as gpu_loop_error:
                print(f"⚠️  GPU processing loop failed: {gpu_loop_error}")
                print("🔄 Falling back to CPU processing")
                use_gpu = False
        
        if not use_gpu:
            # CPU processing loop (fallback or primary)
            if pose_context is None:
                # Need to create CPU processor if we fell back from GPU
                processor = CPUPoseProcessor(threaded_osc, show_fps=show_fps, config=config)
                pose_context, backend_name, window_title = processor.setup_cpu_processor()
                print(f"🚀 Fallback Backend: {backend_name}")
            
            with pose_context as pose:
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
                        print(f"⚠️  CPU frame processing error: {frame_error}")
                        # Continue processing, just skip this frame
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
