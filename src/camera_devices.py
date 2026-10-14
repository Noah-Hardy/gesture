#!/usr/bin/env python3
"""
Camera Devices

Camera names and the macOS camera permission, without new dependencies: a
small AVFoundation query through the Objective-C runtime (ctypes), with
`system_profiler SPCameraDataType` as the fallback for names. Qt-free; both
functions block, so call them from a worker thread.

Index caveat: OpenCV opens `camera.device_id` by position in AVFoundation's
video-device list. This module lists the devices in AVFoundation's own order,
which is what OpenCV iterates, but OpenCV can order or filter devices
differently across versions and macOS releases (virtual cameras, a Continuity
Camera that connects later). The names are a guide to which index is which;
if one is wrong, the index in the option text ("Camera 1 - ...") is what the
engine actually opens.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from typing import List, Optional

_AVFOUNDATION = '/System/Library/Frameworks/AVFoundation.framework/AVFoundation'
_OBJC = '/usr/lib/libobjc.A.dylib'

#: AVAuthorizationStatus -> name
_AUTH = {0: 'undetermined', 1: 'restricted', 2: 'denied', 3: 'authorized'}


# ============================================================================
# OBJECTIVE-C BRIDGE
# ============================================================================
class _ObjC:
    """The few Objective-C calls AVCaptureDevice needs, through libobjc."""

    def __init__(self):
        self.lib = ctypes.cdll.LoadLibrary(_OBJC)
        ctypes.cdll.LoadLibrary(_AVFOUNDATION)
        self.lib.objc_getClass.restype = ctypes.c_void_p
        self.lib.objc_getClass.argtypes = [ctypes.c_char_p]
        self.lib.sel_registerName.restype = ctypes.c_void_p
        self.lib.sel_registerName.argtypes = [ctypes.c_char_p]
        self._send = self.lib.objc_msgSend

    def cls(self, name: str):
        return self.lib.objc_getClass(name.encode())

    def send(self, obj, selector: str, *args, restype=ctypes.c_void_p, argtypes=()):
        self._send.restype = restype
        self._send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
        return self._send(obj, self.lib.sel_registerName(selector.encode()), *args)

    def nsstring(self, text: str):
        return self.send(self.cls('NSString'), 'stringWithUTF8String:', text.encode(),
                         argtypes=[ctypes.c_char_p])

    def to_str(self, nsstring) -> str:
        if not nsstring:
            return ''
        raw = self.send(nsstring, 'UTF8String', restype=ctypes.c_char_p)
        return raw.decode('utf-8', 'replace') if raw else ''


# ============================================================================
# PUBLIC API
# ============================================================================
def camera_authorization() -> str:
    """
    The app's camera permission as macOS reports it.

    Returns:
        'authorized', 'denied', 'restricted', 'undetermined', or 'unknown'
        (not macOS, or the query failed).
    """
    if sys.platform != 'darwin':
        return 'unknown'
    try:
        objc = _ObjC()
        status = objc.send(objc.cls('AVCaptureDevice'), 'authorizationStatusForMediaType:',
                           objc.nsstring('vide'), restype=ctypes.c_long, argtypes=[ctypes.c_void_p])
        return _AUTH.get(int(status), 'unknown')
    except Exception:
        return 'unknown'


def _names_avfoundation() -> Optional[List[str]]:
    objc = _ObjC()
    devices = objc.send(objc.cls('AVCaptureDevice'), 'devicesWithMediaType:', objc.nsstring('vide'),
                        argtypes=[ctypes.c_void_p])
    if not devices:
        return []
    count = objc.send(devices, 'count', restype=ctypes.c_ulong)
    names = []
    for i in range(int(count)):
        dev = objc.send(devices, 'objectAtIndex:', ctypes.c_ulong(i), argtypes=[ctypes.c_ulong])
        names.append(objc.to_str(objc.send(dev, 'localizedName')))
    return names


def _names_system_profiler() -> List[str]:
    out = subprocess.run(['/usr/sbin/system_profiler', 'SPCameraDataType', '-json'],
                         capture_output=True, text=True, timeout=15, check=False).stdout
    items = json.loads(out).get('SPCameraDataType', [])
    return [str(i.get('_name', '')).strip() for i in items if i.get('_name')]


def camera_names() -> List[str]:
    """
    Names of the video capture devices, in device-index order.

    Returns:
        One name per device (index = position); empty if none were found or
        enumeration is unavailable.
    """
    if sys.platform != 'darwin':
        return []
    try:
        names = _names_avfoundation()
        if names is not None:
            return names
    except Exception:
        pass
    try:
        return _names_system_profiler()
    except Exception:
        return []
