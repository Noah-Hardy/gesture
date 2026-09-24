#!/usr/bin/env python3
"""
OSC Protocol Module
One place that decides what goes on the wire. The pose/hand/holistic
processors report tracking events to an OscEmitter; a pluggable formatter
turns each event into (address, args) tuples; the emitter builds the OSC
packets (single datagrams or MTU-sized bundles) and hands them to the
ThreadedOSCSender.

Protocols (config key osc.protocol):
    legacy - the frozen 0.2.x wire format (default until 0.4.0): JSON
             strings with per-landmark type/id, one datagram per message,
             /mp/* status. Must stay byte-identical to
             tests/fixtures/osc_golden.json.
    json   - JSON v2: no per-landmark type/id, top-level "person" on pose
             payloads, null visibility omitted, /gesture/* native-int
             status, per-frame bundles of at most MAX_BUNDLE_BYTES.
    float  - native OSC floats per landmark (FloatFormatter - see the
             ADDING A FORMATTER note below).
"""

# ============================================================================
# IMPORTS
# ============================================================================
import time
from collections import deque

from pythonosc.osc_bundle_builder import IMMEDIATELY, OscBundleBuilder
from pythonosc.osc_message_builder import OscMessageBuilder

from .config import DEFAULT_OSC_PROTOCOL
from .pose_utils import compact_json, get_pose_bounds_with_values, process_landmarks_to_dict


# ============================================================================
# CONSTANTS
# ============================================================================
# Largest bundle datagram the bundled protocols send. Ethernet's MTU is 1500;
# 1400 leaves room for IP/UDP headers plus VPN/tunnel overhead so a bundle
# never gets IP-fragmented (fragmentation is what broke Isadora in #30).
MAX_BUNDLE_BYTES = 1400

# "#bundle\0" + 8-byte timetag, then a 4-byte size prefix per element
_BUNDLE_HEADER_BYTES = 16
_BUNDLE_ELEMENT_OVERHEAD = 4

# /…/tracking holds the highest count seen for this long after detection
# stops reporting it, so one dropped detector frame doesn't flip it to 0.
# Overridable via osc.tracking_hold (seconds).
DEFAULT_TRACKING_HOLD = 0.3

# /…/heartbeat period (seconds)
HEARTBEAT_INTERVAL = 1.0

# Heartbeat counters are OSC int32 ('i') args - wrap rather than overflow
_INT32_MASK = 0x7FFFFFFF


# ============================================================================
# PACKET BUILDING
# ============================================================================
def build_message(address, args):
    """
    Build an OscMessage exactly the way SimpleUDPClient.send_message does,
    so the legacy protocol's bytes don't change: a single non-list value is
    one arg, a list/tuple is one arg per element, None is no args.
    """
    builder = OscMessageBuilder(address=address)
    if args is None:
        pass
    elif isinstance(args, (list, tuple)):
        for value in args:
            builder.add_arg(value)
    else:
        builder.add_arg(args)
    return builder.build()


def pack_bundles(messages, max_bytes=MAX_BUNDLE_BYTES):
    """
    Greedily pack OscMessages, in order, into as few bundles as fit within
    max_bytes each. A message too big to fit even on its own still goes out,
    alone in its own bundle (it will be IP-fragmented, but dropping it would
    be worse) - see oversized_messages() to spot those.

    Returns:
        List of OscBundle
    """
    bundles = []
    builder = None
    size = 0
    for msg in messages:
        element = _BUNDLE_ELEMENT_OVERHEAD + msg.size
        if builder is not None and size + element > max_bytes:
            bundles.append(builder.build())
            builder = None
        if builder is None:
            builder = OscBundleBuilder(IMMEDIATELY)
            size = _BUNDLE_HEADER_BYTES
        builder.add_content(msg)
        size += element
    if builder is not None:
        bundles.append(builder.build())
    return bundles


def oversized_messages(messages, max_bytes=MAX_BUNDLE_BYTES):
    """Messages that can't fit in a max_bytes bundle even by themselves"""
    limit = max_bytes - _BUNDLE_HEADER_BYTES - _BUNDLE_ELEMENT_OVERHEAD
    return [m for m in messages if m.size > limit]


# ============================================================================
# SHARED FORMATTER HELPERS
# ============================================================================
def hand_prefix(handedness):
    """Address prefix for a hand label - anything not "Left" is right_hand (0.2.x rule)"""
    return "left_hand" if handedness.lower() == "left" else "right_hand"


def source_xyz(landmark, transform=None):
    """Landmark (x, y, z) mapped back to source-frame space (identity without a transform)"""
    x, y, z = landmark.x, landmark.y, landmark.z
    if transform is not None:
        x, y = transform.to_source_xy(x, y)
        z = transform.to_source_z(z)
    return x, y, z


def _legacy_world_type(legacy_type):
    """0.2.x world-landmark type string: pose -> pose_world, hand_0 -> hand_world_0"""
    kind, sep, suffix = legacy_type.partition('_')
    return f"{kind}_world{sep}{suffix}"


# ============================================================================
# FORMATTER BASE + REGISTRY
# ============================================================================
# ADDING A FORMATTER: subclass OscFormatter, set `name` (the osc.protocol
# value) and `bundled`, implement the four data hooks (pose, pose_cleared,
# hand, hand_cleared), and decorate the class with @register_formatter.
# Status/tracking/heartbeat already default to the /gesture/* native-int
# channels every new protocol shares. Each hook returns a list of
# (address, args) tuples, where args is a single value or a list of values
# (python-osc infers the type tags: float -> f, int -> i, str -> s).
FORMATTERS = {}


def register_formatter(cls):
    """Class decorator: make a formatter selectable by its `name`"""
    FORMATTERS[cls.name] = cls
    return cls


def make_formatter(protocol):
    """Formatter instance for an osc.protocol value; unknown/unimplemented values fall back to legacy"""
    cls = FORMATTERS.get(protocol)
    if cls is None:
        print(f"⚠️  OSC protocol '{protocol}' not available - using '{DEFAULT_OSC_PROTOCOL}'")
        cls = FORMATTERS[DEFAULT_OSC_PROTOCOL]
    return cls()


class OscFormatter:
    """
    Turns tracking events into (address, args) tuples
    Subclasses implement the data hooks; the channels below are shared
    """

    name = None     # osc.protocol value this formatter answers to
    bundled = True  # True: per-frame MTU-sized bundles. False: one datagram per message

    POSE_STATUS_ADDRESS = "/gesture/pose/status"
    HAND_STATUS_ADDRESS = "/gesture/hand/status"
    POSE_TRACKING_ADDRESS = "/gesture/pose/tracking"
    HAND_TRACKING_ADDRESS = "/gesture/hand/tracking"
    HEARTBEAT_ADDRESS = "/gesture/heartbeat"

    # ------------------------------------------------------------------------
    # Data hooks (every formatter implements these)
    # ------------------------------------------------------------------------

    def pose(self, ts, person, landmarks, world, transform, legacy_type):
        """
        One detected pose

        Args:
            ts: payload timestamp (epoch seconds)
            person: 0-based index of this pose within the frame
            landmarks: normalized landmarks (objects with x, y, z[, visibility]),
                in processing-frame space
            world: world landmarks in metres, or None/empty if unavailable
            transform: LetterboxTransform mapping normalized coords back to
                the source frame (never applied to world landmarks)
            legacy_type: the 0.2.x per-landmark type string for this path
                ("pose", "pose_0", ...). Only LegacyFormatter uses it.
        """
        raise NotImplementedError

    def pose_cleared(self, ts):
        """Pose tracking was lost - clear the receiver's last pose"""
        raise NotImplementedError

    def hand(self, ts, handedness, landmarks, world, transform, legacy_type):
        """
        One detected hand. handedness is MediaPipe's label ("Left", "Right"
        or "Unknown" - anything not "Left" goes on the right_hand channels).
        legacy_type: "hand_0", "hand_left", ...
        """
        raise NotImplementedError

    def hand_cleared(self, ts, handedness):
        """
        Hand tracking was lost. handedness "Left"/"Right" clears one hand;
        None clears both (the legacy mp.solutions path's all-hands clear).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------------
    # Shared channels (native numeric args)
    # ------------------------------------------------------------------------

    def pose_status(self, n):
        return [(self.POSE_STATUS_ADDRESS, int(n))]

    def hand_status(self, n):
        return [(self.HAND_STATUS_ADDRESS, int(n))]

    def pose_tracking(self, n):
        return [(self.POSE_TRACKING_ADDRESS, int(n))]

    def hand_tracking(self, n):
        return [(self.HAND_TRACKING_ADDRESS, int(n))]

    def heartbeat(self, fps, queued, dropped, sent):
        """fps as f, then OSC queue depth, dropped and sent packet counts as i"""
        return [(self.HEARTBEAT_ADDRESS, [
            float(fps), int(queued) & _INT32_MASK, int(dropped) & _INT32_MASK, int(sent) & _INT32_MASK
        ])]


# ============================================================================
# LEGACY FORMATTER (frozen 0.2.x wire format)
# ============================================================================
@register_formatter
class LegacyFormatter(OscFormatter):
    """
    The exact 0.2.x output, quirks included (per-landmark type/id,
    "visibility": null, the duplicate /mp/status 0 inside a pose clear, the
    handedness-less all-hands clear). Only the additive channels - tracking
    and heartbeat - are new, and they use native args under /mp/ and /hand/.
    Do not "fix" anything here: tests/fixtures/osc_golden.json pins it.
    """

    name = "legacy"
    bundled = False

    POSE_TRACKING_ADDRESS = "/mp/tracking"
    HAND_TRACKING_ADDRESS = "/hand/tracking"
    HEARTBEAT_ADDRESS = "/mp/heartbeat"

    def pose(self, ts, person, landmarks, world, transform, legacy_type):
        out = []
        lm_dicts = process_landmarks_to_dict(landmarks, legacy_type, transform) if landmarks else []
        world_dicts = process_landmarks_to_dict(world, _legacy_world_type(legacy_type)) if world else []
        if lm_dicts:
            out.append(("/pose/raw", compact_json({"timestamp": ts, "landmarks": lm_dicts})))
        if world_dicts:
            out.append(("/pose/world", compact_json({"timestamp": ts, "landmarks": world_dicts})))
        if landmarks:
            out.append(("/pose/raw_bounds", compact_json(get_pose_bounds_with_values(landmarks, transform))))
        if world_dicts:
            # World landmarks are already in real-world metres - no transform
            out.append(("/pose/world_bounds", compact_json(get_pose_bounds_with_values(world))))
        return out

    def pose_cleared(self, ts):
        empty = compact_json({"timestamp": ts, "landmarks": []})
        return [
            ("/pose/raw", empty),
            ("/pose/raw_bounds", compact_json({})),
            ("/pose/world", empty),
            ("/pose/world_bounds", compact_json({})),
            ("/mp/status", compact_json({"status": 0})),
        ]

    def hand(self, ts, handedness, landmarks, world, transform, legacy_type):
        prefix = hand_prefix(handedness)
        out = []
        lm_dicts = process_landmarks_to_dict(landmarks, legacy_type, transform) if landmarks else []
        world_dicts = process_landmarks_to_dict(world, _legacy_world_type(legacy_type)) if world else []
        if lm_dicts:
            out.append((f"/{prefix}/raw", compact_json(
                {"timestamp": ts, "handedness": handedness, "landmarks": lm_dicts})))
        if world_dicts:
            out.append((f"/{prefix}/world", compact_json(
                {"timestamp": ts, "handedness": handedness, "landmarks": world_dicts})))
        if landmarks:
            out.append((f"/{prefix}/bounds", compact_json(get_pose_bounds_with_values(landmarks, transform))))
        if world_dicts:
            # World landmarks are already in real-world metres - no transform
            out.append((f"/{prefix}/world_bounds", compact_json(get_pose_bounds_with_values(world))))
        return out

    def hand_cleared(self, ts, handedness):
        if handedness is None:
            # mp.solutions path: both hands, no handedness key, plus a status 0
            empty = compact_json({"timestamp": ts, "landmarks": []})
            out = []
            for prefix in ("left_hand", "right_hand"):
                out += [
                    (f"/{prefix}/raw", empty),
                    (f"/{prefix}/world", empty),
                    (f"/{prefix}/bounds", compact_json({})),
                    (f"/{prefix}/world_bounds", compact_json({})),
                ]
            out.append(("/hand/status", compact_json({"status": 0})))
            return out
        prefix = hand_prefix(handedness)
        empty = compact_json({"timestamp": ts, "handedness": handedness, "landmarks": []})
        return [
            (f"/{prefix}/raw", empty),
            (f"/{prefix}/world", empty),
            (f"/{prefix}/bounds", compact_json({})),
            (f"/{prefix}/world_bounds", compact_json({})),
        ]

    def pose_status(self, n):
        return [("/mp/status", compact_json({"status": n}))]

    def hand_status(self, n):
        return [("/hand/status", compact_json({"status": n}))]


# ============================================================================
# JSON FORMATTER (v2)
# ============================================================================
def landmarks_to_v2(landmarks, transform=None, visibility=True):
    """
    JSON v2 landmark list: array index is the landmark id, so no per-landmark
    type/id (#51); visibility only when the landmark actually carries one.
    visibility=False drops it outright - for world landmarks, where it only
    repeats the normalized landmark's value.
    """
    out = []
    for lm in landmarks:
        x, y, z = source_xyz(lm, transform)
        d = {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}
        vis = getattr(lm, "visibility", None) if visibility else None
        if vis is not None:
            d["visibility"] = round(vis, 3)
        out.append(d)
    return out


def world_bounds_v2(world):
    """Bounds of world landmarks without the (redundant) visibility on each extreme"""
    bounds = get_pose_bounds_with_values(world)
    for extreme in bounds.values():
        extreme.pop("visibility", None)
    return bounds


@register_formatter
class JsonFormatter(OscFormatter):
    """
    JSON v2 on the same data addresses as legacy. Pose payloads and pose
    bounds carry a top-level "person" index in place of the pose_0/pose_1
    type strings. Bounds keep their "id" - there it names which landmark is
    the extreme, which the array position can't tell you. World landmarks
    and world bounds carry no visibility: it duplicates the normalized
    landmark's, and dropping it keeps /pose/world inside one bundle.
    """

    name = "json"
    bundled = True

    def pose(self, ts, person, landmarks, world, transform, legacy_type):
        out = []
        if landmarks:
            out.append(("/pose/raw", compact_json(
                {"timestamp": ts, "person": person, "landmarks": landmarks_to_v2(landmarks, transform)})))
        if world:
            out.append(("/pose/world", compact_json(
                {"timestamp": ts, "person": person, "landmarks": landmarks_to_v2(world, visibility=False)})))
        if landmarks:
            bounds = get_pose_bounds_with_values(landmarks, transform)
            out.append(("/pose/raw_bounds", compact_json({"person": person, **bounds})))
        if world:
            # World landmarks are already in real-world metres - no transform
            out.append(("/pose/world_bounds", compact_json({"person": person, **world_bounds_v2(world)})))
        return out

    def pose_cleared(self, ts):
        empty = compact_json({"timestamp": ts, "landmarks": []})
        return [
            ("/pose/raw", empty),
            ("/pose/raw_bounds", compact_json({})),
            ("/pose/world", empty),
            ("/pose/world_bounds", compact_json({})),
        ]

    def hand(self, ts, handedness, landmarks, world, transform, legacy_type):
        prefix = hand_prefix(handedness)
        out = []
        if landmarks:
            out.append((f"/{prefix}/raw", compact_json(
                {"timestamp": ts, "handedness": handedness, "landmarks": landmarks_to_v2(landmarks, transform)})))
        if world:
            out.append((f"/{prefix}/world", compact_json(
                {"timestamp": ts, "handedness": handedness, "landmarks": landmarks_to_v2(world, visibility=False)})))
        if landmarks:
            out.append((f"/{prefix}/bounds", compact_json(get_pose_bounds_with_values(landmarks, transform))))
        if world:
            out.append((f"/{prefix}/world_bounds", compact_json(world_bounds_v2(world))))
        return out

    def hand_cleared(self, ts, handedness):
        out = []
        for label in (("Left", "Right") if handedness is None else (handedness,)):
            prefix = hand_prefix(label)
            empty = compact_json({"timestamp": ts, "handedness": label, "landmarks": []})
            out += [
                (f"/{prefix}/raw", empty),
                (f"/{prefix}/world", empty),
                (f"/{prefix}/bounds", compact_json({})),
                (f"/{prefix}/world_bounds", compact_json({})),
            ]
        return out


# ============================================================================
# TRACKING HYSTERESIS
# ============================================================================
class TrackingHysteresis:
    """
    Debounced presence count behind /…/tracking (#53)

    The raw status channels report 0 on every frame the async detector
    didn't finish - by design, they never cache a count. Tracking is the
    debounced view: the highest count reported within the last `hold`
    seconds, so it rises immediately and only falls once detection has
    really stopped reporting that many.
    """

    def __init__(self, hold=DEFAULT_TRACKING_HOLD, clock=time.monotonic):
        self.hold = hold
        self._clock = clock
        self._recent = deque()  # (time, count) for nonzero reports inside the hold window

    def update(self, n):
        """Record one status report"""
        if n > 0:
            self._recent.append((self._clock(), n))

    @property
    def value(self):
        """Current debounced count"""
        now = self._clock()
        while self._recent and now - self._recent[0][0] > self.hold:
            self._recent.popleft()
        return max((n for _, n in self._recent), default=0)

    def reset(self):
        self._recent.clear()


# ============================================================================
# OSC EMITTER
# ============================================================================
class OscEmitter:
    """
    What the processors talk to instead of the raw sender

    Frame protocol (main loop):
        emitter.heartbeat()          # top of every loop iteration, even on read failures
        emitter.begin_frame(ts)      # before the processors run
        ... processors call pose()/hand()/…_cleared()/…_status() ...
        emitter.end_frame()          # appends tracking, flushes bundles
    and on shutdown: emitter.clear_all(), then sender.stop(flush=True).

    Legacy sends every message immediately as its own datagram. Bundled
    protocols buffer a frame's messages and send them at end_frame() as
    one or more bundles of at most max_bundle_bytes; anything emitted
    outside a frame is bundled and sent right away.
    """

    def __init__(self, sender, protocol=DEFAULT_OSC_PROTOCOL, config=None, clock=time.monotonic):
        """
        Args:
            sender: ThreadedOSCSender (anything with send_packet() and get_stats())
            protocol: osc.protocol value - legacy, json or float
            config: Configuration object (optional) - reads osc.tracking_hold
            clock: monotonic time source (injectable for tests)
        """
        self.sender = sender
        self.formatter = make_formatter(protocol)
        self.protocol = self.formatter.name
        self.max_bundle_bytes = MAX_BUNDLE_BYTES
        self._clock = clock

        hold = config.get('osc', 'tracking_hold', DEFAULT_TRACKING_HOLD) if config else DEFAULT_TRACKING_HOLD
        self._trackers = {
            'pose': TrackingHysteresis(hold, clock),
            'hand': TrackingHysteresis(hold, clock),
        }

        self._frame_open = False
        self._frame_ts = None
        self._pending = []                # bundled protocols: this frame's messages
        self._status_this_frame = set()   # 'pose'/'hand' channels that reported status this frame
        self._channels_used = set()       # every channel family ever emitted (for clear_all)

        self._last_heartbeat = None
        self._frames_since_heartbeat = 0

    # ------------------------------------------------------------------------
    # Packet output
    # ------------------------------------------------------------------------

    def _emit(self, messages):
        """Queue or send a formatter's (address, args) tuples"""
        if not messages:
            return
        if not self.formatter.bundled:
            for address, args in messages:
                self.sender.send_packet(build_message(address, args))
        elif self._frame_open:
            self._pending.extend(messages)
        else:
            self._send_bundles(messages)

    def _send_bundles(self, messages):
        built = [build_message(address, args) for address, args in messages]
        for bundle in pack_bundles(built, self.max_bundle_bytes):
            self.sender.send_packet(bundle)

    def _ts(self, ts):
        if ts is not None:
            return ts
        return self._frame_ts if self._frame_open else time.time()

    # ------------------------------------------------------------------------
    # Frame boundaries
    # ------------------------------------------------------------------------

    def begin_frame(self, ts=None):
        """Start collecting one processing-loop iteration's output"""
        if self._frame_open:
            # Previous iteration never reached end_frame (it raised) - ship
            # what it had rather than letting it bleed into this frame
            self.end_frame()
        self._frame_open = True
        self._frame_ts = ts if ts is not None else time.time()
        self._status_this_frame.clear()

    def end_frame(self):
        """Append tracking for every channel that reported status, then flush"""
        if not self._frame_open:
            return
        messages = []
        if 'pose' in self._status_this_frame:
            messages += self.formatter.pose_tracking(self._trackers['pose'].value)
        if 'hand' in self._status_this_frame:
            messages += self.formatter.hand_tracking(self._trackers['hand'].value)
        self._emit(messages)

        self._frame_open = False
        self._frames_since_heartbeat += 1
        pending, self._pending = self._pending, []
        if pending:
            self._send_bundles(pending)

    # ------------------------------------------------------------------------
    # Tracking events (called by the processors)
    # ------------------------------------------------------------------------

    def pose(self, person, landmarks, world=None, transform=None, legacy_type="pose", ts=None):
        """One detected pose - see OscFormatter.pose for the arguments"""
        self._channels_used.add('pose')
        self._emit(self.formatter.pose(self._ts(ts), person, landmarks, world, transform, legacy_type))

    def pose_cleared(self, ts=None):
        """Pose lost - send the clear once, on the detected -> not detected transition"""
        self._channels_used.add('pose')
        self._emit(self.formatter.pose_cleared(self._ts(ts)))

    def hand(self, handedness, landmarks, world=None, transform=None, legacy_type="hand", ts=None):
        """One detected hand - see OscFormatter.hand for the arguments"""
        self._channels_used.add('hand')
        self._emit(self.formatter.hand(self._ts(ts), handedness, landmarks, world, transform, legacy_type))

    def hand_cleared(self, handedness=None, ts=None):
        """Hand lost - "Left"/"Right" for one hand, None for both"""
        self._channels_used.add('hand')
        self._emit(self.formatter.hand_cleared(self._ts(ts), handedness))

    def pose_status(self, n):
        """Raw per-frame pose count: 0 whenever nothing is tracked this frame - never cached"""
        self._report_status('pose', n)
        self._emit(self.formatter.pose_status(n))

    def hand_status(self, n):
        """Raw per-frame hand count: 0 whenever nothing is tracked this frame - never cached"""
        self._report_status('hand', n)
        self._emit(self.formatter.hand_status(n))

    def _report_status(self, channel, n):
        self._channels_used.add(channel)
        self._status_this_frame.add(channel)
        self._trackers[channel].update(n)

    # ------------------------------------------------------------------------
    # Heartbeat and shutdown
    # ------------------------------------------------------------------------

    def heartbeat(self, fps=None):
        """
        Send the 1 Hz health message if one is due (#54). Call it every loop
        iteration - including ones where the camera read failed - so a
        receiver can tell "engine alive, nobody in frame" from "engine gone".

        Args:
            fps: frames per second to report. Defaults to the rate of
                end_frame() calls since the previous heartbeat.

        Returns:
            True if a heartbeat was sent
        """
        now = self._clock()
        if self._last_heartbeat is not None and now - self._last_heartbeat < HEARTBEAT_INTERVAL:
            return False
        if fps is None:
            elapsed = now - self._last_heartbeat if self._last_heartbeat is not None else 0
            fps = self._frames_since_heartbeat / elapsed if elapsed > 0 else 0.0
        self._last_heartbeat = now
        self._frames_since_heartbeat = 0
        stats = self.sender.get_stats()
        self._emit(self.formatter.heartbeat(fps, stats['queued'], stats['dropped'], stats['sent']))
        return True

    def clear_all(self, ts=None):
        """
        Shutdown clear (#55): empty every channel family this session used
        and zero its status and tracking, so a receiver doesn't hold the last
        pose forever. Follow with sender.stop(flush=True) so it actually leaves.
        """
        if self._frame_open:
            self.end_frame()
        ts = ts if ts is not None else time.time()
        messages = []
        if 'pose' in self._channels_used:
            messages += self.formatter.pose_cleared(ts)
            messages += self.formatter.pose_status(0)
            messages += self.formatter.pose_tracking(0)
        if 'hand' in self._channels_used:
            messages += self.formatter.hand_cleared(ts, "Left")
            messages += self.formatter.hand_cleared(ts, "Right")
            messages += self.formatter.hand_status(0)
            messages += self.formatter.hand_tracking(0)
        for tracker in self._trackers.values():
            tracker.reset()
        self._emit(messages)

    def get_stats(self):
        """Sender statistics (sent/dropped/queued) - for the processors' FPS log line"""
        return self.sender.get_stats()
