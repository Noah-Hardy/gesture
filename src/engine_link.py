#!/usr/bin/env python3
"""
Launcher Side of the Engine IPC (#65)
Qt-free client for the channel src/engine_channel.py serves. The Qt
EngineController wraps this and turns its callbacks into signals.

Typical use (one EngineLink per engine process):

    link = EngineLink(on_status=..., on_reply=..., on_closed=...)
    proc = subprocess.Popen([*engine_argv, *link.argv], pass_fds=link.pass_fds)
    link.spawned()                   # close our copy of the child's socket end
    ...
    link.send({"cmd": "set", "key": "osc.port", "value": 9000})
    seq_and_frame = link.read_frame()   # (frame_seq, HxWx3 BGR ndarray) or None
    ...
    link.close()                     # after the engine has exited

The contract the engine sees:
    --ipc-fd <N>      the child end of an AF_UNIX socket pair, inherited at fd N
    --ipc-shm <name>  shared-memory segment the engine creates for preview frames
`pass_fds` keeps the descriptor number, so argv can name it. QProcess users
must call os.set_inheritable on `child_fd` (already done here) and spawn with
a mechanism that keeps the descriptor open in the child.

If the launcher dies, the kernel closes its end and the engine exits within
about a second; if the engine dies, the reader thread sees EOF and calls
on_closed.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import socket
import subprocess
import threading

from .ipc_protocol import (IPC_FD_FLAG, IPC_SHM_FLAG, FrameReader, LineReader, encode_message,
                           make_shm_name)


# ============================================================================
# ENGINE LINK
# ============================================================================
class EngineLink:
    """One launcher-side endpoint: socket pair, shared-memory name, reader thread"""

    def __init__(self, on_status=None, on_reply=None, on_closed=None, frames=True):
        """
        Args:
            on_status: callable(dict) for every status message
            on_reply: callable(dict) for {"ack": key} / {"error": msg, "key": key}
            on_closed: callable() once, when the engine's end closes
            All three run on the reader thread.
            frames: Request preview frames (passes --ipc-shm); False skips
                the shared-memory segment
        """
        self._on_status = on_status
        self._on_reply = on_reply
        self._on_closed = on_closed
        self.last_status = {}
        self.closed = threading.Event()

        self._sock, self._child = socket.socketpair()
        os.set_inheritable(self._child.fileno(), True)
        self._sock.settimeout(1.0)  # sends only; the reader polls with this timeout too
        self._send_lock = threading.Lock()

        self.shm_name = make_shm_name() if frames else None
        self._frames = FrameReader(self.shm_name) if frames else None

        self._reader = threading.Thread(target=self._read_loop, name='engine-link-reader', daemon=True)
        self._reader.start()

    # ------------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------------

    @property
    def child_fd(self):
        """Descriptor number of the engine's socket end (valid until spawned())"""
        return self._child.fileno()

    @property
    def argv(self):
        """Engine arguments naming the socket descriptor and the preview segment"""
        args = [IPC_FD_FLAG, str(self.child_fd)]
        if self.shm_name:
            args += [IPC_SHM_FLAG, self.shm_name]
        return args

    @property
    def pass_fds(self):
        """For subprocess.Popen(pass_fds=...)"""
        return (self.child_fd,)

    def spawn(self, engine_argv, **popen_kwargs):
        """
        Start the engine with the channel attached

        Args:
            engine_argv: The engine command line without the IPC arguments
            **popen_kwargs: Passed to subprocess.Popen

        Returns:
            The subprocess.Popen
        """
        proc = subprocess.Popen([*engine_argv, *self.argv], pass_fds=self.pass_fds, **popen_kwargs)
        self.spawned()
        return proc

    def spawned(self):
        """
        Close our copy of the engine's socket end

        Must follow the spawn: while the launcher still holds that end, the
        reader never sees EOF when the engine dies, and a later child would
        inherit it.
        """
        try:
            self._child.close()
        except OSError:
            pass

    # ------------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------------

    def send(self, command):
        """
        Send a control command

        Args:
            command: dict such as {"cmd": "stop"}, {"cmd": "set", "key": ..., "value": ...}
                or {"cmd": "visible", "value": bool}

        Returns:
            False if the engine is gone or not reading
        """
        if self.closed.is_set():
            return False
        try:
            with self._send_lock:
                self._sock.sendall(encode_message(command))
            return True
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------------------
    # Frames
    # ------------------------------------------------------------------------

    def frame_info(self):
        """{"width", "height", "stride"} once the engine has created the segment, else None"""
        return self._frames.info() if self._frames is not None else None

    def read_frame(self, only_new=True):
        """
        Copy out the newest preview frame

        Args:
            only_new: Return None unless a frame newer than the last one
                returned is available

        Returns:
            (frame_seq, HxWx3 uint8 BGR ndarray) or None
        """
        return self._frames.read(only_new) if self._frames is not None else None

    # ------------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------------

    def _read_loop(self):
        lines = LineReader()
        while not self.closed.is_set():
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                data = b''
            if not data:
                break
            for message in lines.feed(data):
                self._dispatch(message)
        self.closed.set()
        if self._on_closed is not None:
            self._call(self._on_closed)

    def _dispatch(self, message):
        if 'ack' in message or 'error' in message:
            if self._on_reply is not None:
                self._call(self._on_reply, message)
            return
        self.last_status = message
        if self._on_status is not None:
            self._call(self._on_status, message)

    @staticmethod
    def _call(callback, *args):
        try:
            callback(*args)
        except Exception as e:
            print(f"⚠️  Engine link callback failed: {e}")

    # ------------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------------

    def close(self):
        """
        Tear the link down: stop the reader, close the sockets, detach and
        unlink the preview segment (a backstop for an engine that died
        without cleaning up). Idempotent.
        """
        self.closed.set()
        self.spawned()
        for action in (lambda: self._sock.shutdown(socket.SHUT_RDWR), self._sock.close):
            try:
                action()
            except OSError:
                pass
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=2.0)
        if self._frames is not None:
            self._frames.close(unlink=True)
