#!/usr/bin/env python3
"""
Engine <-> Launcher IPC Protocol (#65)
The wire format both ends share. Qt-free and free of mediapipe/cv2 so the
launcher can import it without paying for the engine's stack.

Transport (one AF_UNIX socketpair plus one shared-memory segment):

    Status    engine -> launcher   line-delimited JSON, about 10 Hz
    Control   launcher -> engine   line-delimited JSON commands
    Replies   engine -> launcher   {"ack": key} / {"error": msg, "key": key}
                                   on the same stream, interleaved with status
    Frames    engine -> launcher   shared-memory double buffer, see FrameWriter

Status message (every field always present):
    phase        one of src.runtime.ALL_PHASES
    reason       only on fault phases and 'stopped' (may be empty)
    fps          processed frames per second over the last ~1 s
    latency_ms   mean frame read -> OSC enqueue time since the previous status
    detections   {"pose": n, "hands": n}; 0 whenever nothing is tracked
    queue        packets waiting in the OSC sender queue
    dropped      cumulative packets dropped by the sender
    sent         cumulative packets sent (one bundle = one packet)
    per_address  {address: cumulative message count}; the UI diffs for rates
    uptime       seconds since the engine process started
    frame_seq    sequence number of the newest preview frame (0 = none yet)

Control commands:
    {"cmd": "stop"}
    {"cmd": "set", "key": "section.key", "value": ...}   live keys only
    {"cmd": "visible", "value": bool}                    preview consumed or not
"""

# ============================================================================
# IMPORTS
# ============================================================================
import json
import os
import secrets
import struct
from contextlib import contextmanager
from multiprocessing import resource_tracker, shared_memory


# ============================================================================
# CONSTANTS
# ============================================================================
STATUS_INTERVAL = 0.1     # Seconds between status messages
MAX_LINE_BYTES = 1 << 20  # A longer unterminated line is garbage - drop it

IPC_FD_FLAG = '--ipc-fd'
IPC_SHM_FLAG = '--ipc-shm'


# ============================================================================
# LINE-DELIMITED JSON
# ============================================================================
def encode_message(message):
    """
    Serialize one message as a newline-terminated JSON line

    Args:
        message: JSON-serializable dict

    Returns:
        bytes ending in b"\\n"
    """
    return json.dumps(message, separators=(',', ':'), ensure_ascii=False).encode('utf-8') + b'\n'


def decode_message(line):
    """
    Parse one line

    Args:
        line: bytes or str, with or without the trailing newline

    Returns:
        The decoded dict

    Raises:
        ValueError: the line is not a JSON object
    """
    if isinstance(line, bytes):
        line = line.decode('utf-8', errors='replace')
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError("IPC message must be a JSON object")
    return message


class LineReader:
    """Reassembles messages from arbitrary recv() chunks"""

    def __init__(self):
        self._buffer = b''

    def feed(self, data):
        """
        Args:
            data: bytes just received

        Returns:
            List of decoded dicts completed by this chunk; malformed lines
            are skipped rather than killing the channel
        """
        self._buffer += data
        messages = []
        while True:
            end = self._buffer.find(b'\n')
            if end < 0:
                break
            line, self._buffer = self._buffer[:end], self._buffer[end + 1:]
            if not line.strip():
                continue
            try:
                messages.append(decode_message(line))
            except ValueError:
                continue
        if len(self._buffer) > MAX_LINE_BYTES:
            self._buffer = b''
        return messages


# ============================================================================
# SHARED MEMORY HELPERS
# ============================================================================
@contextmanager
def _untracked():
    """
    Keep multiprocessing's resource tracker out of a SharedMemory call

    Python 3.11 registers even a plain attach with the resource tracker, which
    then unlinks the segment when the attaching process exits and warns about
    leaks - and starting the tracker relaunches sys.executable, which in the
    frozen app is the whole bundle. Ownership is explicit here instead: the
    engine creates and unlinks, the launcher unlinks as a backstop.
    """
    real_register, real_unregister = resource_tracker.register, resource_tracker.unregister
    resource_tracker.register = lambda *args, **kwargs: None
    resource_tracker.unregister = lambda *args, **kwargs: None
    try:
        yield
    finally:
        resource_tracker.register, resource_tracker.unregister = real_register, real_unregister


def make_shm_name():
    """
    A fresh segment name. macOS caps POSIX shm names near 30 characters, so
    this stays short: 'gst' + pid + 6 hex digits.
    """
    return f"gst{os.getpid()}{secrets.token_hex(3)}"


def unlink_shm_name(name):
    """Remove a segment by name; a segment that is already gone is fine"""
    try:
        with _untracked():
            segment = shared_memory.SharedMemory(name=name)
            segment.close()
            segment.unlink()
    except (FileNotFoundError, OSError):
        pass


# ============================================================================
# FRAME DOUBLE BUFFER
# ============================================================================
# Segment layout (little endian):
#   header, HEADER_SIZE bytes:
#       4s  magic            b'GSTF'
#       I   version
#       I   width            pixels
#       I   height           pixels
#       I   stride           bytes per row (BGR, 3 channels, no padding)
#       I   slot_size        bytes per buffer
#       Q   latest_seq       newest complete frame (0 = none yet)
#       Q   slot0_version    seqlock word for buffer 0
#       Q   slot1_version    seqlock word for buffer 1
#   buffer 0, buffer 1: slot_size bytes each, frame n lives in buffer n % 2
#
# The writer marks a slot's version odd while it writes (seq << 1 | 1) and
# even when done (seq << 1), then publishes latest_seq. A reader copies the
# slot and keeps the frame only if the version did not change underneath it.
FRAME_MAGIC = b'GSTF'
FRAME_VERSION = 1
_HEADER = struct.Struct('<4sIIIIIQQQ')
HEADER_SIZE = 64
_OFF_LATEST = 24
_OFF_SLOT_VERSION = (32, 40)


def frame_segment_size(width, height):
    """Bytes needed for a width x height BGR double buffer"""
    return HEADER_SIZE + 2 * width * height * 3


class FrameWriter:
    """Engine side: creates the segment and publishes frames into it"""

    def __init__(self, name, width, height):
        """
        Args:
            name: Segment name the launcher passed in (--ipc-shm)
            width, height: Frame size in pixels - the processing resolution

        Raises:
            OSError: the segment cannot be created
        """
        import numpy as np
        self._np = np
        self.name = name
        self.width = int(width)
        self.height = int(height)
        self.stride = self.width * 3
        self.slot_size = self.stride * self.height
        with _untracked():
            try:
                self._shm = shared_memory.SharedMemory(
                    name=name, create=True, size=frame_segment_size(self.width, self.height))
            except FileExistsError:
                # A previous engine under the same name died without cleanup
                unlink_shm_name(name)
                self._shm = shared_memory.SharedMemory(
                    name=name, create=True, size=frame_segment_size(self.width, self.height))
        buf = self._shm.buf
        buf[:HEADER_SIZE] = b'\0' * HEADER_SIZE
        _HEADER.pack_into(buf, 0, FRAME_MAGIC, FRAME_VERSION, self.width, self.height,
                          self.stride, self.slot_size, 0, 0, 0)
        self._slots = [
            np.ndarray((self.height, self.width, 3), np.uint8, buf, HEADER_SIZE + i * self.slot_size)
            for i in range(2)
        ]
        self.seq = 0

    def write(self, frame):
        """
        Publish one BGR frame

        Args:
            frame: uint8 HxWx3 array. A different size is resized to the
                segment's (the engine's processing resolution), since the
                launcher sized its view from the header

        Returns:
            The new frame sequence number
        """
        np = self._np
        if frame.shape[:2] != (self.height, self.width):
            import cv2
            frame = cv2.resize(frame, (self.width, self.height))
        seq = self.seq + 1
        slot = seq & 1
        buf = self._shm.buf
        struct.pack_into('<Q', buf, _OFF_SLOT_VERSION[slot], (seq << 1) | 1)
        np.copyto(self._slots[slot], frame, casting='unsafe')
        struct.pack_into('<Q', buf, _OFF_SLOT_VERSION[slot], seq << 1)
        struct.pack_into('<Q', buf, _OFF_LATEST, seq)
        self.seq = seq
        return seq

    def close(self):
        """Release and unlink the segment (idempotent)"""
        if self._shm is None:
            return
        shm, self._shm = self._shm, None
        self._slots = []
        try:
            with _untracked():
                shm.close()
                shm.unlink()
        except (BufferError, FileNotFoundError, OSError):
            pass


class FrameReader:
    """Launcher side: attaches to the engine's segment and copies frames out"""

    def __init__(self, name):
        self.name = name
        self._shm = None
        self._last_seq = 0

    def _attach(self):
        if self._shm is not None:
            return True
        try:
            with _untracked():
                shm = shared_memory.SharedMemory(name=self.name)
        except (FileNotFoundError, OSError):
            return False  # the engine has not created it yet
        magic, version = struct.unpack_from('<4sI', shm.buf, 0)
        if magic != FRAME_MAGIC or version != FRAME_VERSION:
            shm.close()
            return False
        self._shm = shm
        return True

    def info(self):
        """
        Returns:
            {"width", "height", "stride"} once the engine has created the
            segment, else None
        """
        if not self._attach():
            return None
        _, _, width, height, stride, _, _, _, _ = _HEADER.unpack_from(self._shm.buf, 0)
        return {'width': width, 'height': height, 'stride': stride}

    def read(self, only_new=True):
        """
        Copy out the newest complete frame

        Args:
            only_new: Return None when nothing newer than the last frame
                returned is available

        Returns:
            (seq, ndarray HxWx3 BGR copy), or None when there is no frame,
            no new frame, or the frame was overwritten mid-copy
        """
        if not self._attach():
            return None
        import numpy as np
        buf = self._shm.buf
        _, _, width, height, _, slot_size, _, _, _ = _HEADER.unpack_from(buf, 0)
        for _ in range(3):
            seq = struct.unpack_from('<Q', buf, _OFF_LATEST)[0]
            if seq == 0 or (only_new and seq <= self._last_seq):
                return None
            slot = seq & 1
            before = struct.unpack_from('<Q', buf, _OFF_SLOT_VERSION[slot])[0]
            if before != seq << 1:
                continue  # the writer already moved on; take the newer frame
            offset = HEADER_SIZE + slot * slot_size
            frame = np.frombuffer(buf, np.uint8, slot_size, offset).reshape(height, width, 3).copy()
            after = struct.unpack_from('<Q', buf, _OFF_SLOT_VERSION[slot])[0]
            if after == before:
                self._last_seq = seq
                return seq, frame
        return None

    def close(self, unlink=False):
        """Detach; with unlink=True also remove the segment (backstop for a dead engine)"""
        shm, self._shm = self._shm, None
        if shm is not None:
            try:
                shm.close()
            except (BufferError, OSError):
                pass
        if unlink:
            unlink_shm_name(self.name)
