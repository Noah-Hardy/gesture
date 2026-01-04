#!/usr/bin/env python3
"""
NDI Video Capture Module
Direct NDI stream access for better performance than virtual cameras
Provides OpenCV-compatible interface for seamless integration
"""

# ============================================================================
# IMPORTS
# ============================================================================
import numpy as np
import cv2

# Try to import NDI library (optional dependency)
try:
    import NDIlib as ndi
    NDI_AVAILABLE = True
except ImportError:
    NDI_AVAILABLE = False
    print("⚠️  NDI library not available. Install with: pip install ndi-python")


# ============================================================================
# NDI CAPTURE CLASS
# ============================================================================
class NDICapture:
    """
    OpenCV-compatible video capture from NDI sources
    Drop-in replacement for cv2.VideoCapture when using NDI
    Provides lower latency than using NDI virtual cameras
    """
    
    def __init__(self, source_name=None, timeout_ms=5000):
        """
        Initialize NDI capture
        
        Args:
            source_name: Name of NDI source to connect to (e.g., "MY-PC (OBS)").
                        If None, connects to first available source.
            timeout_ms: Timeout for finding sources and receiving frames (milliseconds)
        """
        if not NDI_AVAILABLE:
            raise RuntimeError("NDI library not available")
        
        self.source_name = source_name
        self.timeout_ms = timeout_ms
        self.receiver = None
        self.finder = None
        self.connected_source = None
        self._is_opened = False
        self._frame_width = 0
        self._frame_height = 0
        self._fps = 30.0
        
        # Initialize NDI library
        if not ndi.initialize():
            raise RuntimeError("Failed to initialize NDI")
        
        self._connect()
    
    # ------------------------------------------------------------------------
    # Private connection method
    # ------------------------------------------------------------------------
    
    def _connect(self):
        """Find and connect to NDI source on the network"""
        # Create finder to discover sources
        self.finder = ndi.find_create_v2()
        if self.finder is None:
            raise RuntimeError("Failed to create NDI finder")
        
        print("🔍 Searching for NDI sources...")
        
        # Wait for sources to be found
        sources = []
        for _ in range(50):  # Try for up to 5 seconds
            ndi.find_wait_for_sources(self.finder, 100)
            sources = ndi.find_get_current_sources(self.finder)
            if sources:
                break
        
        if not sources:
            print("❌ No NDI sources found")
            return
        
        # List available sources
        print(f"📡 Found {len(sources)} NDI source(s):")
        for i, source in enumerate(sources):
            print(f"   [{i}] {source.ndi_name}")
        
        # Select source
        selected_source = None
        if self.source_name:
            # Find by name
            for source in sources:
                if self.source_name.lower() in source.ndi_name.lower():
                    selected_source = source
                    break
            if not selected_source:
                print(f"⚠️  Source '{self.source_name}' not found, using first available")
                selected_source = sources[0]
        else:
            selected_source = sources[0]
        
        print(f"✅ Connecting to: {selected_source.ndi_name}")
        
        # Create receiver
        recv_settings = ndi.RecvCreateV3()
        recv_settings.source_to_connect_to = selected_source
        recv_settings.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA  # OpenCV-compatible
        recv_settings.bandwidth = ndi.RECV_BANDWIDTH_HIGHEST
        
        self.receiver = ndi.recv_create_v3(recv_settings)
        if self.receiver is None:
            raise RuntimeError("Failed to create NDI receiver")
        
        # Connect
        ndi.recv_connect(self.receiver, selected_source)
        self.connected_source = selected_source
        self._is_opened = True
        
        # Get initial frame to determine resolution
        print("⏳ Waiting for first frame...")
        for _ in range(100):  # Try for up to 10 seconds
            frame_type, video, _, _ = ndi.recv_capture_v2(self.receiver, 100)
            if frame_type == ndi.FRAME_TYPE_VIDEO:
                self._frame_width = video.xres
                self._frame_height = video.yres
                self._fps = video.frame_rate_N / video.frame_rate_D if video.frame_rate_D else 30.0
                ndi.recv_free_video_v2(self.receiver, video)
                print(f"📐 Resolution: {self._frame_width}x{self._frame_height} @ {self._fps:.1f}fps")
                break
        else:
            print("⚠️  Could not determine resolution from first frame")
    
    # ------------------------------------------------------------------------
    # OpenCV-compatible public methods
    # ------------------------------------------------------------------------
    
    def isOpened(self):
        """Check if capture is opened (OpenCV compatibility)"""
        return self._is_opened
    
    def read(self):
        """
        Read a frame from NDI source (OpenCV compatibility)
        Retries multiple times with short timeouts for responsiveness
        
        Returns:
            Tuple of (success: bool, frame: np.ndarray or None)
        """
        if not self._is_opened or self.receiver is None:
            return False, None
        
        # Try multiple times with shorter timeout for better responsiveness
        for _ in range(10):  # Try up to 10 times with 500ms each = 5s total
            frame_type, video, _, _ = ndi.recv_capture_v2(self.receiver, 500)
            
            if frame_type == ndi.FRAME_TYPE_VIDEO:
                # Convert to numpy array
                frame = np.copy(video.data)
                
                # Update resolution if changed
                self._frame_width = video.xres
                self._frame_height = video.yres
                
                # Free the NDI frame
                ndi.recv_free_video_v2(self.receiver, video)
                
                # NDI gives us BGRX (4 channels), convert to BGR (3 channels) for OpenCV
                if frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                
                return True, frame
            
            elif frame_type == ndi.FRAME_TYPE_NONE:
                # No frame yet, keep trying
                continue
        
        # No frame after all retries
        return False, None
    
    def get(self, prop_id):
        """
        Get capture property (OpenCV compatibility)
        Supports CAP_PROP_FRAME_WIDTH, CAP_PROP_FRAME_HEIGHT, CAP_PROP_FPS
        
        Args:
            prop_id: OpenCV property ID constant
            
        Returns:
            Property value as float, or 0.0 if not supported
        """
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._frame_width)
        elif prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._frame_height)
        elif prop_id == cv2.CAP_PROP_FPS:
            return self._fps
        return 0.0
    
    def set(self, prop_id, value):
        """Set capture property (OpenCV compatibility - mostly no-op for NDI)"""
        # NDI resolution is determined by the source, not configurable on receiver
        return False
    
    # ------------------------------------------------------------------------
    # Cleanup and utility methods
    # ------------------------------------------------------------------------
    
    def release(self):
        """Release NDI resources and cleanup (OpenCV compatibility)"""
        if self.receiver:
            ndi.recv_destroy(self.receiver)
            self.receiver = None
        if self.finder:
            ndi.find_destroy(self.finder)
            self.finder = None
        self._is_opened = False
        ndi.destroy()
        print("✅ NDI capture released")
    
    def getBackendName(self):
        """Get backend name (OpenCV compatibility)"""
        return "NDI"
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.release()


# ============================================================================
# NDI SOURCE DISCOVERY UTILITY
# ============================================================================
def list_ndi_sources():
    """
    List all available NDI sources on the network
    Searches for 5 seconds to discover sources
    
    Returns:
        List of NDI source names as strings
    """
    if not NDI_AVAILABLE:
        print("NDI library not available")
        return []
    
    if not ndi.initialize():
        print("Failed to initialize NDI")
        return []
    
    finder = ndi.find_create_v2()
    if finder is None:
        print("Failed to create NDI finder")
        ndi.destroy()
        return []
    
    print("Searching for NDI sources (5 seconds)...")
    sources = []
    for _ in range(50):
        ndi.find_wait_for_sources(finder, 100)
        sources = ndi.find_get_current_sources(finder)
    
    source_names = [s.ndi_name for s in sources]
    
    ndi.find_destroy(finder)
    ndi.destroy()
    
    return source_names


# Test if run directly
if __name__ == "__main__":
    print("=== NDI Source Discovery ===")
    sources = list_ndi_sources()
    if sources:
        print(f"\nFound {len(sources)} source(s):")
        for name in sources:
            print(f"  - {name}")
        
        print("\n=== Testing Capture ===")
        cap = NDICapture()
        if cap.isOpened():
            import time
            start = time.time()
            frames = 0
            while frames < 60:
                ret, frame = cap.read()
                if ret:
                    frames += 1
                    if frames % 10 == 0:
                        print(f"Received {frames} frames")
            elapsed = time.time() - start
            print(f"\nReceived {frames} frames in {elapsed:.2f}s = {frames/elapsed:.1f} FPS")
            cap.release()
    else:
        print("No NDI sources found")
