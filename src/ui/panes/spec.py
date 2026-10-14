#!/usr/bin/env python3
"""
Settings Pane Spec

The five settings panes as data (the design's `PANES`, updated for what 0.3/0.4
ship): each pane is a title, a blurb and a list of rows. The generic renderer in
`_render.py` turns rows into widgets bound to the EditModel, so adding a setting
means adding a row here.

Row kinds:
    head, sep                  section heading / divider
    choice                     SegmentedChoice          options: (value, label) pairs or strings
    select                     Select                   options come from a live source
    num                        TextField, whole numbers limits: (low, high[, message])
    decimal                    TextField, decimal number limits: (low, high)
    text                       TextField                wide=True for a host
    toggle                     Toggle
    range                      RangeSlider (0-1, 0.05 steps)
    color                      ColorSwatch + Choose...  stored as a BGR list
    status, table, code, path  display rows fed by `source`
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ...config import Config


# ============================================================================
# SPEC TYPES
# ============================================================================
@dataclass(frozen=True)
class Row:
    """
    One settings row.

    Attributes:
        kind: See the module docstring.
        label: Row label (section text for `head`).
        key: Config path `section.key`; None for display-only rows.
        options: choice options.
        note, warn: Faint helper text / amber warning.
        restart: True if the engine only reads the value at startup.
        limits: (low, high[, message]) for num / decimal rows.
        wide: Wide text field (hosts).
        codec: Named config <-> control conversion (see _render.CODECS).
        default: Value assumed when the key is absent from the config.
        source: Name of the dynamic content provider for display rows and selects.
        bar_label: Label used in the pending-change bar when `label` is ambiguous.
    """
    kind: str
    label: str = ''
    key: Optional[str] = None
    options: Tuple[Any, ...] = ()
    note: Optional[str] = None
    warn: Optional[str] = None
    restart: bool = False
    limits: Optional[tuple] = None
    wide: bool = False
    codec: Optional[str] = None
    default: Any = None
    source: Optional[str] = None
    bar_label: Optional[str] = None


@dataclass(frozen=True)
class PaneSpec:
    """A settings pane: key (rail key), title, blurb and rows."""
    key: str
    title: str
    blurb: str
    rows: Tuple[Row, ...]


def head(label: str) -> Row:
    """Section heading."""
    return Row('head', label)


SEP = Row('sep')

PORT_NOTE = 'Ports run 1–65535; anything under 1024 needs root'
FPS_NOTE = '0 is uncapped; otherwise 1–240'


def _num(label, key, limits, **kw) -> Row:
    return Row('num', label, key, limits=limits, **kw)


def _conf(label, key, bar_label) -> Row:
    return Row('range', label, key, bar_label=bar_label)


# ============================================================================
# PANES
# ============================================================================
INPUT = PaneSpec('input', 'Input', 'Changes apply on restart.', (
    Row('choice', 'Source', 'camera.use_ndi', options=('Camera', 'NDI'), codec='source', restart=True),
    Row('select', 'Camera device', 'camera.device_id', source='camera_devices', restart=True),
    Row('select', 'NDI source', 'camera.ndi_source', source='ndi_sources', restart=True,
        note='Sources appear as they’re found'),
    Row('choice', 'NDI bandwidth', 'camera.ndi_bandwidth', options=('lowest', 'highest'), restart=True,
        note='Lowest is a ~640x360 proxy, plenty for tracking'),
    Row('status', 'Camera permission', source='camera_permission', note='Checked on every start'),
    SEP,
    _num('Capture width', 'camera.width', (160, 3840), restart=True),
    _num('Capture height', 'camera.height', (120, 2160), restart=True),
    _num('Capture FPS', 'camera.fps', (1, 240), restart=True),
    _num('Processing width', 'camera.processing_width', (160, 3840), restart=True),
    _num('Processing height', 'camera.processing_height', (120, 2160), restart=True),
    _num('Buffer size', 'camera.buffer_size', (1, 16), restart=True,
         note='cv2.CAP_PROP_BUFFERSIZE — minimum 1'),
    _num('Reconnect timeout', 'camera.reconnect_timeout', (0, 3600), restart=True,
         note='Seconds; 0 retries indefinitely'),
))

OSC = PaneSpec('osc', 'OSC Output',
               'Host, port and queue apply live. Tracking mode and output format need a restart.', (
    Row('text', 'Host', 'osc.host', wide=True),
    _num('Port', 'osc.port', (1, 65535, PORT_NOTE)),
    _num('Send queue size', 'osc.queue_size', (Config.MIN_OSC_QUEUE_SIZE, 1000), note='Drops oldest when full'),
    SEP,
    Row('choice', 'Output format', 'osc.protocol',
        options=(('legacy', 'Legacy (0.2.x)'), ('json', 'JSON'), ('float', 'Float')), restart=True,
        note='Legacy (0.2.x) is the old format, kept through 0.x. Float suits lossy networks.'),
    Row('choice', 'Tracking mode', 'ui.mode', options=('pose', 'hand', 'all'), restart=True),
    Row('table', 'Address map', source='address_map'),
    Row('code', 'Payload sample', source='payload_sample'),
))

TRACKING = PaneSpec('tracking', 'Tracking',
                    'Higher is steadier; lower catches partial bodies. Thresholds apply live.', (
    head('Pose'),
    Row('choice', 'Model', 'mediapipe.pose_model_type', options=('lite', 'full', 'heavy'), restart=True),
    Row('table', 'Frame cost on this machine', source='frame_cost',
        note='Measured once per model, on a still portrait. Your camera feed will differ.'),
    _num('Number of poses', 'mediapipe.num_poses', (1, 4), restart=True,
         note='More than 1 disables holistic'),
    _conf('Min detection confidence', 'mediapipe.min_detection_confidence', 'Pose min detection confidence'),
    _conf('Min tracking confidence', 'mediapipe.min_tracking_confidence', 'Pose min tracking confidence'),
    _conf('Min pose presence confidence', 'mediapipe.min_pose_presence_confidence',
          'Pose min presence confidence'),
    SEP,
    head('Hands'),
    _num('Number of hands', 'hand.num_hands', (0, 2), restart=True),
    _conf('Min detection confidence', 'hand.min_detection_confidence', 'Hand min detection confidence'),
    _conf('Min presence confidence', 'hand.min_presence_confidence', 'Hand min presence confidence'),
    _conf('Min tracking confidence', 'hand.min_tracking_confidence', 'Hand min tracking confidence'),
))

PREVIEW = PaneSpec('preview', 'Preview', 'Display only — never affects OSC.', (
    Row('toggle', 'Draw landmarks over the feed', 'display.draw_landmarks'),
    Row('toggle', 'Mirror preview', 'display.mirror_preview'),
    Row('toggle', 'Render preview in background', 'display.render_in_background',
        note='Tracking continues either way'),
    SEP,
    Row('color', 'Landmark colour', 'display.landmark_color', codec='bgr'),
    _num('Landmark thickness', 'display.landmark_thickness', (1, 10)),
    _num('Landmark radius', 'display.landmark_radius', (1, 20)),
    Row('color', 'Connection colour', 'display.connection_color', codec='bgr'),
    _num('Connection thickness', 'display.connection_thickness', (1, 10)),
    _num('Connection radius', 'display.connection_radius', (1, 20)),
))

ADVANCED = PaneSpec('advanced', 'Advanced', 'Backend changes apply on restart.', (
    head('Performance'),
    _num('Target FPS', 'performance.target_fps', (0, 240, FPS_NOTE), note='0 = uncapped'),
    Row('toggle', 'Show FPS / stats line', 'performance.show_fps'),
    Row('toggle', 'Enable garbage collection', 'performance.gc_enabled'),
    _num('Max pending frames', 'performance.max_pending_frames', (1, 8), note='1 = lowest latency'),
    Row('decimal', 'Tracking hold', 'osc.tracking_hold', limits=(0, 5), default=0.3,
        note='Seconds a tracking count holds after detection stops reporting it'),
    SEP,
    head('Backend'),
    Row('toggle', 'Force CPU delegate', 'performance.force_cpu', restart=True),
    Row('toggle', 'Force GPU delegate', 'performance.force_gpu', restart=True,
        warn='Leaks memory on Apple Silicon'),
    Row('toggle', 'Separate pose and hand models', 'performance.no_holistic', restart=True),
    SEP,
    head('Updates'),
    Row('toggle', 'Check for updates on launch', 'updates.check_on_launch'),
    Row('toggle', 'Include pre-release builds', 'updates.include_prereleases'),
    Row('path', 'config.json', source='config_path'),
))

#: In rail order
PANES = {p.key: p for p in (INPUT, OSC, TRACKING, PREVIEW, ADVANCED)}
