#!/usr/bin/env python3
"""
Live Settings (#65)
Applies the launcher's live `set` commands to a running engine and decides,
each frame, whether the preview needs drawing and publishing. The set of keys
and their validation live in src/live_keys.py; this is the engine-side effect
of each one.

Everything here runs on the processing thread, between frames - the reader
thread only queues commands (see EngineChannel.drain_sets) - so none of it
races the processors.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from pythonosc import udp_client

from .live_keys import DRAWING_KEYS, LANDMARKER_KEYS


# ============================================================================
# LIVE SETTINGS
# ============================================================================
class LiveSettings:
    """Owns the mutable pieces of a running session that live keys touch"""

    def __init__(self, config, emitter, sender, frame_clock, pose_processor, hand_processor,
                 pose_landmarker, hand_landmarker, channel, preview_window=False):
        """
        Args:
            config: Configuration object (mutated in memory only - the
                launcher owns config.json)
            emitter: OscEmitter
            sender: ThreadedOSCSender (its client is swapped on host/port changes)
            frame_clock: FrameClock pacing the loop
            pose_processor: The pose (or holistic) processor, or None
            hand_processor: The hand processor, or None
            pose_landmarker, hand_landmarker: Their landmarkers; replaced here
                on a threshold change, so cleanup must read them back from
                this object
            channel: EngineChannel or NullChannel
            preview_window: A local cv2 window is showing frames (--preview-window)
        """
        self.config = config
        self.emitter = emitter
        self.sender = sender
        self.frame_clock = frame_clock
        self.pose_processor = pose_processor
        self.hand_processor = hand_processor
        self.pose_landmarker = pose_landmarker
        self.hand_landmarker = hand_landmarker
        self.channel = channel
        self.preview_window = preview_window

        display = config.get('display')
        self.mirror = bool(display.get('mirror_preview', False))
        self.draw_landmarks = bool(display.get('draw_landmarks', True))
        self.render_in_background = bool(display.get('render_in_background', False))
        self.update_draw_state()

    # ------------------------------------------------------------------------
    # Per-frame preview decisions
    # ------------------------------------------------------------------------

    @property
    def preview_visible(self):
        """Is anything looking at the preview right now"""
        return self.preview_window or self.channel.visible or self.render_in_background

    @property
    def publish_frames(self):
        """Should this frame be written to the shared buffer"""
        return self.channel.frames_wanted and (self.channel.visible or self.render_in_background)

    def update_draw_state(self):
        """
        Draw landmarks only when the result will be seen: display.draw_landmarks
        is on and the preview is visible (or display.render_in_background keeps
        it rendering while hidden). Drawing never affects OSC output.
        """
        enabled = self.draw_landmarks and self.preview_visible
        for processor in (self.pose_processor, self.hand_processor):
            if processor is not None:
                processor.draw_enabled = enabled

    # ------------------------------------------------------------------------
    # Applying commands
    # ------------------------------------------------------------------------

    def apply_pending(self):
        """
        Apply every `set` the launcher queued since the last frame, then reply

        Replies go out through the channel: {"ack": key} on success,
        {"error": message, "key": key} on failure. Threshold changes in one
        batch share a single landmarker rebuild.
        """
        pending = self.channel.drain_sets()
        if not pending:
            return
        applied = []   # (key, count) accepted so far
        rollback = {}  # key -> previous value, for a failed landmarker rebuild
        rebuild = set()

        for key, value, count in pending:
            section, name = key.split('.', 1)
            old = self.config.get(section, name)
            try:
                self._apply(key, value)
            except Exception as e:
                self.channel.error(key, f"{key}: {e}", count)
                continue
            if key in LANDMARKER_KEYS and value != old:
                rollback.setdefault(key, old)
                rebuild |= self._affected(key)
            applied.append((key, count, value))

        if rebuild:
            try:
                self._rebuild(rebuild)
            except Exception as e:
                for key, old in rollback.items():
                    section, name = key.split('.', 1)
                    self.config.set(section, name, old)
                for key, count, _ in [a for a in applied if a[0] in rollback]:
                    self.channel.error(key, f"{key}: could not rebuild the landmarker: {e}", count)
                applied = [a for a in applied if a[0] not in rollback]

        for key, count, value in applied:
            print(f"⚙️  Live setting {key} = {value}")
            self.channel.ack(key, count)

    def _apply(self, key, value):
        """Apply one validated value; raises ValueError / OSError when it cannot take effect"""
        section, name = key.split('.', 1)

        if key == 'osc.host' or key == 'osc.port':
            host = value if key == 'osc.host' else self.config.get('osc', 'host')
            port = value if key == 'osc.port' else self.config.get('osc', 'port')
            try:
                client = udp_client.SimpleUDPClient(host, port, allow_broadcast=True)
            except OSError as e:
                raise ValueError(f"cannot resolve {host}:{port}: {e}") from e
            self.sender.client = client
        elif key == 'osc.queue_size':
            self.sender.set_queue_size(value)
        elif key == 'osc.tracking_hold':
            self.emitter.set_tracking_hold(value)
        elif key == 'performance.target_fps':
            self.frame_clock.set_interval(1.0 / value if value > 0 else 0)
        elif key == 'performance.show_fps':
            if self.pose_processor is not None:
                self.pose_processor.set_show_fps(value)
            # Same rule as startup: with a pose/holistic processor running, the
            # hand processor stays quiet so the line isn't printed twice
            if self.hand_processor is not None and self.pose_processor is None:
                self.hand_processor.set_show_fps(value)
        elif key == 'display.mirror_preview':
            self.mirror = value
        elif key == 'display.draw_landmarks':
            self.draw_landmarks = value
        elif key == 'display.render_in_background':
            self.render_in_background = value

        self.config.set(section, name, value)

        if key in DRAWING_KEYS:
            for processor in (self.pose_processor, self.hand_processor):
                if processor is not None:
                    processor.refresh_display_specs()
        if key in ('display.draw_landmarks', 'display.render_in_background'):
            self.update_draw_state()

    # ------------------------------------------------------------------------
    # Landmarker rebuild
    # ------------------------------------------------------------------------

    def _affected(self, key):
        """Which landmarker slots ('pose', 'hand') a threshold key reaches"""
        holistic = self.pose_processor is not None and self.pose_processor.label == 'Holistic'
        if key.startswith('mediapipe.'):
            return {'pose'}
        if holistic:
            # The holistic model only takes hand.min_presence_confidence; the
            # other two hand thresholds are stored but have nothing to change
            return {'pose'} if key == 'hand.min_presence_confidence' else set()
        return {'hand'}

    def _rebuild(self, slots):
        """Rebuild the landmarkers in `slots` with the current config"""
        if 'pose' in slots and self.pose_processor is not None:
            self.pose_landmarker = self.pose_processor.rebuild_landmarker(self.pose_landmarker)
        if 'hand' in slots and self.hand_processor is not None:
            self.hand_landmarker = self.hand_processor.rebuild_landmarker(self.hand_landmarker)
