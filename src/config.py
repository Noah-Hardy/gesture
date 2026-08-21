#!/usr/bin/env python3
"""
Configuration Management Module
Handles JSON config files with environment variable overrides
Cross-platform compatible configuration system
"""

# ============================================================================
# IMPORTS
# ============================================================================
import json
import os
import sys
import tempfile
from typing import Dict, Any


# ============================================================================
# PATH RESOLUTION
# ============================================================================
def default_config_path() -> str:
    """Resolve the config file path (writable location when frozen)."""
    if getattr(sys, 'frozen', False):
        d = os.path.join(os.path.expanduser('~/Library/Application Support'), 'mp-osc')
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, 'config.json')
    return 'config.json'


# ============================================================================
# CONFIGURATION CLASS
# ============================================================================
class Config:
    """
    Configuration manager with file and environment variable support
    Provides centralized configuration for the application
    """
    
    # Default configuration values
    DEFAULT_CONFIG = {
        "osc": {
            "host": "127.0.0.1",
            "port": 1234,
            "queue_size": 10
        },
        "camera": {
            "device_id": 0,
            "width": 640,
            "height": 480,
            "fps": 30,
            "buffer_size": 1,
            "processing_width": 640,
            "processing_height": 480,
            "use_ndi": False,
            "ndi_source": ""
        },
        "mediapipe": {
            "model_complexity": 0,
            "min_detection_confidence": 0.7,
            "min_tracking_confidence": 0.5,
            "min_pose_presence_confidence": 0.5,
            "smooth_landmarks": True,
            "enable_segmentation": False,
            "num_poses": 1,  # Note: Only supported in GPU mode (MediaPipe Tasks), CPU mode limited to 1
            "pose_model_type": "lite"  # lite, full, or heavy
        },
        "hand": {
            "num_hands": 2,
            "model_complexity": 1,
            "min_detection_confidence": 0.5,
            "min_presence_confidence": 0.5,
            "min_tracking_confidence": 0.5,
            "left_landmark_color": [0, 255, 0],      # Green for left hand
            "left_connection_color": [0, 200, 0],
            "right_landmark_color": [255, 0, 0],    # Red for right hand (BGR)
            "right_connection_color": [200, 0, 0]
        },
        "performance": {
            "show_fps": False,
            "target_fps": 0,  # 0 = uncapped, set to 30 for stable 30fps cap
            "gc_enabled": True,  # Enable/disable garbage collection (disable for smoother FPS)
            "gc_interval": 60,  # Garbage collection interval in frames (higher = smoother but more memory)
            "force_cpu": False,  # Force the CPU delegate (launch-time, GUI/Settings only)
            "force_gpu": False,  # Force the GPU delegate - has a memory leak on Apple Silicon (launch-time)
            "force_legacy": False,  # Use MediaPipe's legacy synchronous API (launch-time, GUI/Settings only)
            "no_holistic": False  # In "all" mode, use separate pose+hand models instead of holistic (launch-time)
        },
        "display": {
            "show_window": True,
            "window_title": "MediaPipe OSC Pose Detection",
            "mirror_preview": False,  # Flip the preview horizontally (display only - OSC data is unaffected)
            "landmark_color": [245, 117, 66],
            "connection_color": [245, 66, 230],
            "landmark_thickness": 1,
            "landmark_radius": 2,
            "connection_thickness": 1,
            "connection_radius": 1
        },
        "updates": {
            "check_on_launch": True,      # Silently check GitHub for a newer release on launch
            "include_prereleases": True,  # Whether pre-release tags count as an available update
            "last_check": 0,              # Epoch seconds of the last completed check
            "last_etag": "",              # HTTP ETag from the last check (for If-None-Match)
            "last_seen_version": "",      # Newest version the last check saw; when it's newer
                                          # than this build, checks skip the ETag so the full
                                          # release details come back for the dialog
            "skipped_version": "",        # Tag the user chose "Skip This Version" on
            "rate_limited_until": 0       # Epoch seconds; checks are suppressed until this passes
        },
        "ui": {
            "input_section_open": True,
            "osc_section_open": True,
            "model_section_open": False,
            "log_section_open": True
        }
    }
    
    def __init__(self, config_file: str = None):
        """Initialize configuration manager"""
        self.config_file = config_file or default_config_path()
        self.config = self._load_config()
        self._apply_platform_defaults()
    
    def _apply_platform_defaults(self):
        """Apply platform-specific default configurations (expandable for future use)"""
        pass
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from file with fallback to defaults
        
        Returns:
            Dict containing merged configuration (defaults + file + env)
        """
        config = self.DEFAULT_CONFIG.copy()
        
        # Load from file if it exists
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    file_config = json.load(f)
                config = self._deep_merge(config, file_config)
                print(f"📋 Loaded configuration from {self.config_file}")
            except (json.JSONDecodeError, IOError) as e:
                print(f"⚠️  Failed to load config file {self.config_file}: {e}")
                print("🔄 Using default configuration")
        else:
            print(f"📄 Config file {self.config_file} not found, using defaults")
        
        config = self._sanitize(config)

        # Override with environment variables
        config = self._apply_env_overrides(config)

        return config

    def _sanitize(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Repair values that older builds allowed to be saved out of range"""
        # cv2.CAP_PROP_BUFFERSIZE needs at least 1 frame of buffering
        try:
            buffer_size = int(config['camera'].get('buffer_size', 1))
        except (TypeError, ValueError):
            buffer_size = 1
        config['camera']['buffer_size'] = max(1, buffer_size)
        return config
    
    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """
        Recursively merge two dictionaries
        
        Args:
            base: Base dictionary
            override: Dictionary with override values
            
        Returns:
            Merged dictionary
        """
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply environment variable overrides to configuration
        Supports type conversion based on original config value types
        
        Returns:
            Configuration dict with environment overrides applied
        """
        # Environment variable to config path mappings
        env_mappings = {
            "MP_OSC_HOST": ("osc", "host"),
            "MP_OSC_PORT": ("osc", "port"),
            "MP_CAMERA_ID": ("camera", "device_id"),
            "MP_CAMERA_WIDTH": ("camera", "width"),
            "MP_CAMERA_HEIGHT": ("camera", "height"),
            "MP_SHOW_FPS": ("performance", "show_fps"),
            "MP_MIRROR_PREVIEW": ("display", "mirror_preview"),
            "MP_MIN_DETECTION_CONFIDENCE": ("mediapipe", "min_detection_confidence"),
            "MP_MIN_TRACKING_CONFIDENCE": ("mediapipe", "min_tracking_confidence")
        }
        
        for env_var, (section, key) in env_mappings.items():
            if env_var in os.environ:
                value = os.environ[env_var]
                # Type conversion based on original type
                if isinstance(config[section][key], bool):
                    value = value.lower() in ('true', '1', 'yes', 'on')
                elif isinstance(config[section][key], int):
                    try:
                        value = int(value)
                    except ValueError:
                        print(f"⚠️  Invalid integer value for {env_var}: {value}")
                        continue
                elif isinstance(config[section][key], float):
                    try:
                        value = float(value)
                    except ValueError:
                        print(f"⚠️  Invalid float value for {env_var}: {value}")
                        continue
                
                config[section][key] = value
                print(f"🔧 Override from {env_var}: {section}.{key} = {value}")
        
        return config
    
    # ------------------------------------------------------------------------
    # Public configuration access methods
    # ------------------------------------------------------------------------
    
    def get(self, section: str, key: str = None, default=None) -> Any:
        """
        Get configuration value
        
        Args:
            section: Configuration section name
            key: Optional key within section
            default: Default value if not found
            
        Returns:
            Configuration value or default
        """
        if key is None:
            return self.config.get(section, default)
        return self.config.get(section, {}).get(key, default)
    
    def set(self, section: str, key: str, value: Any) -> None:
        """
        Set configuration value (runtime only, not persisted)
        
        Args:
            section: Configuration section name
            key: Key within section
            value: Value to set
        """
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
    
    # ------------------------------------------------------------------------
    # Configuration file operations
    # ------------------------------------------------------------------------
    
    def save(self) -> None:
        """
        Save current configuration to file

        Writes to a temp file in the same directory and atomically renames it
        into place (os.replace), so a crash or power loss mid-write cannot
        leave config.json truncated - the reader either sees the old file or
        the fully-written new one, never a partial one.
        """
        directory = os.path.dirname(os.path.abspath(self.config_file)) or '.'
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.config.', suffix='.tmp')
            with os.fdopen(fd, 'w') as f:
                json.dump(self.config, f, indent=2)
            os.replace(tmp_path, self.config_file)
            tmp_path = None
            print(f"💾 Configuration saved to {self.config_file}")
        except (IOError, OSError) as e:
            print(f"❌ Failed to save config file: {e}")
        finally:
            if tmp_path is not None and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    
    def create_default_config_file(self) -> None:
        """Create a default configuration file"""
        if not os.path.exists(self.config_file):
            self.config = self.DEFAULT_CONFIG.copy()
            self.save()
            print(f"📝 Created default config file: {self.config_file}")
        else:
            print(f"📄 Config file already exists: {self.config_file}")
    
    def print_config(self) -> None:
        """Print current configuration"""
        print("📋 Current Configuration:")
        print(json.dumps(self.config, indent=2))


# ============================================================================
# VALIDATION HELPERS
# ============================================================================
def valid_port(value) -> bool:
    """True if value is an int in the valid TCP/UDP port range"""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 65535


def valid_unit_float(value) -> bool:
    """True if value is a number in [0.0, 1.0], for confidence thresholds"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


# ============================================================================
# GLOBAL CONFIGURATION INSTANCE
# ============================================================================
# Singleton configuration instance for the application
config = Config()


def get_config() -> Config:
    """Get the global configuration instance"""
    return config
