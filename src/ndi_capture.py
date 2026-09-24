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
# SOURCE SELECTION
# ============================================================================
# camera.ndi_bandwidth values. "lowest" asks the sender for its proxy
# stream (~640x360) - the engine letterboxes every frame down to the
# processing size anyway, so fully decoding 1080p only to shrink it is
# wasted work. "highest" is the full-resolution program stream.
NDI_BANDWIDTHS = ('lowest', 'highest')
DEFAULT_NDI_BANDWIDTH = 'lowest'


def select_ndi_source(sources, wanted):
    """
    Pick the NDI source to connect to

    Match order: exact name (case-insensitive), then a substring that
    matches exactly one source. An ambiguous or missing name matches
    nothing - the old "substring, else sources[0]" rule could silently
    connect to the wrong machine ("Studio" matching "Studio-2"), which
    in a show is worse than failing loudly.

    Args:
        sources: Discovered NDI sources (anything with .ndi_name)
        wanted: Configured source name, or empty/None for "first found"

    Returns:
        (source or None, reason) - reason is a one-line explanation
        for the log when no source was chosen
    """
    if not sources:
        return None, "no NDI sources found"
    if not wanted:
        return sources[0], ""

    wanted_lower = wanted.lower()
    for source in sources:
        if source.ndi_name.lower() == wanted_lower:
            return source, ""

    partial = [s for s in sources if wanted_lower in s.ndi_name.lower()]
    if len(partial) == 1:
        return partial[0], ""
    if partial:
        names = ", ".join(s.ndi_name for s in partial)
        return None, f"'{wanted}' matches more than one source ({names}) - use the full name"
    return None, f"no source named '{wanted}'"


# ============================================================================
# NDI CAPTURE CLASS
# ============================================================================
class NDICapture:
    """
    OpenCV-compatible video capture from NDI sources
    Drop-in replacement for cv2.VideoCapture when using NDI
    Provides lower latency than using NDI virtual cameras
    """
    
    def __init__(self, source_name=None, timeout_ms=5000, bandwidth=DEFAULT_NDI_BANDWIDTH):
        """
        Initialize NDI capture
        
        Args:
            source_name: Name of NDI source to connect to (e.g., "MY-PC (OBS)").
                        If None, connects to first available source. See
                        select_ndi_source for how the name is matched.
            timeout_ms: Timeout for finding sources and receiving frames (milliseconds)
            bandwidth: "lowest" (proxy stream, default) or "highest" - see NDI_BANDWIDTHS
        """
        if not NDI_AVAILABLE:
            raise RuntimeError("NDI library not available")
        
        self.source_name = source_name
        self.timeout_ms = timeout_ms
        self.bandwidth = bandwidth if bandwidth in NDI_BANDWIDTHS else DEFAULT_NDI_BANDWIDTH
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
        
        # Select source - never silently falls back to a different one
        selected_source, reason = select_ndi_source(sources, self.source_name)
        if selected_source is None:
            print(f"❌ {reason}")
            return
        if not self.source_name and len(sources) > 1:
            print("⚠️  No NDI source name configured - using the first one found")
        
        print(f"✅ Connecting to: {selected_source.ndi_name}")
        self._open_receiver(selected_source)
        
        # Get initial frame to determine resolution
        print("⏳ Waiting for first frame...")
        for _ in range(100):  # Try for up to 10 seconds
            frame_type, video, audio, metadata = ndi.recv_capture_v2(self.receiver, 100)
            if frame_type == ndi.FRAME_TYPE_VIDEO:
                self._frame_width = video.xres
                self._frame_height = video.yres
                self._fps = video.frame_rate_N / video.frame_rate_D if video.frame_rate_D else 30.0
                ndi.recv_free_video_v2(self.receiver, video)
                print(f"📐 Resolution: {self._frame_width}x{self._frame_height} @ {self._fps:.1f}fps")
                break
            elif frame_type == ndi.FRAME_TYPE_AUDIO:
                ndi.recv_free_audio_v2(self.receiver, audio)
            elif frame_type == ndi.FRAME_TYPE_METADATA:
                ndi.recv_free_metadata(self.receiver, metadata)
        else:
            print("⚠️  Could not determine resolution from first frame")

    def _open_receiver(self, source):
        """Create a receiver for `source` and mark the capture open"""
        recv_settings = ndi.RecvCreateV3()
        recv_settings.source_to_connect_to = source
        recv_settings.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA  # OpenCV-compatible
        recv_settings.bandwidth = (ndi.RECV_BANDWIDTH_HIGHEST if self.bandwidth == 'highest'
                                   else ndi.RECV_BANDWIDTH_LOWEST)
        
        self.receiver = ndi.recv_create_v3(recv_settings)
        if self.receiver is None:
            raise RuntimeError("Failed to create NDI receiver")
        
        # Connect
        ndi.recv_connect(self.receiver, source)
        self.connected_source = source
        self._is_opened = True

    def reconnect(self):
        """
        Rebuild the receiver after the sender went away (#31)

        Senders dropping and coming back is normal in production (an OBS
        restart, a laptop sleeping). Re-finds the source through the
        retained finder - no NDI re-initialize, no 5s discovery wait - so
        ReconnectingCapture can call this every few failed reads without
        stalling the processing loop. Reconnects to the source it was
        connected to, even when no name was configured.

        Returns:
            True if a receiver is connected again
        """
        if self.receiver is not None:
            ndi.recv_destroy(self.receiver)
            self.receiver = None
        self._is_opened = False

        if self.finder is None:
            self.finder = ndi.find_create_v2()
            if self.finder is None:
                return False

        ndi.find_wait_for_sources(self.finder, 100)
        sources = ndi.find_get_current_sources(self.finder)
        wanted = self.connected_source.ndi_name if self.connected_source is not None else self.source_name
        source, reason = select_ndi_source(sources, wanted)
        if source is None:
            print(f"⚠️  NDI reconnect: {reason}")
            return False

        print(f"🔄 NDI reconnecting to: {source.ndi_name}")
        self._open_receiver(source)
        return True
    
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
        
        # Try multiple times with shorter timeout for better responsiveness.
        # Capped at ~0.5s (was 1s) so a dead sender costs the processing
        # loop half a second per read, not a full second, while
        # ReconnectingCapture works on getting it back
        for _ in range(10):  # Try up to 10 times with 50ms each = 0.5s total
            frame_type, video, audio, metadata = ndi.recv_capture_v2(self.receiver, 50)
            
            if frame_type == ndi.FRAME_TYPE_VIDEO:
                # Update resolution if changed
                self._frame_width = video.xres
                self._frame_height = video.yres

                # NDI gives us BGRX (4 channels), convert to BGR (3 channels) for OpenCV.
                # cv2.cvtColor allocates a fresh output array, so we can convert
                # directly from the NDI buffer (valid until freed) and skip the
                # redundant full-frame np.copy.
                if video.data.ndim == 3 and video.data.shape[2] == 4:
                    frame = cv2.cvtColor(video.data, cv2.COLOR_BGRA2BGR)
                else:
                    # Defensive: unexpected layout - copy so we never return a
                    # view into freed NDI memory
                    frame = np.copy(video.data)

                # Free the NDI frame - CRITICAL to prevent memory leak
                ndi.recv_free_video_v2(self.receiver, video)

                return True, frame
            
            elif frame_type == ndi.FRAME_TYPE_AUDIO:
                # Free audio frames to prevent memory leak
                ndi.recv_free_audio_v2(self.receiver, audio)
                continue
            
            elif frame_type == ndi.FRAME_TYPE_METADATA:
                # Free metadata frames to prevent memory leak
                ndi.recv_free_metadata(self.receiver, metadata)
                continue
            
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
    
    print("Searching for NDI sources (up to 5 seconds)...")
    sources = []
    stable_count = 0
    last_count = 0
    for _ in range(50):  # Up to 5 seconds maximum
        ndi.find_wait_for_sources(finder, 100)
        sources = ndi.find_get_current_sources(finder)
        # Early exit: once sources are non-empty and stable for ~1 second
        # (same count for 10 consecutive iterations), stop searching
        if sources and len(sources) == last_count:
            stable_count += 1
            if stable_count >= 10:
                break
        else:
            stable_count = 0
            last_count = len(sources)
    
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
