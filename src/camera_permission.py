#!/usr/bin/env python3
"""
Camera Permission Check (#65)
Reads macOS's camera authorization status so the engine can report
'permission_denied' instead of a generic camera failure. Uses libobjc through
ctypes (no new dependencies): [AVCaptureDevice authorizationStatusForMediaType:
AVMediaTypeVideo]. Never prompts - the prompt appears when OpenCV opens the
camera. Off macOS, or if anything about the lookup fails, the answer is
'unknown' and callers fall back to the generic no_camera fault.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import ctypes
import ctypes.util
import platform

#: AVAuthorizationStatus values -> names
_STATUS_NAMES = {0: 'not_determined', 1: 'restricted', 2: 'denied', 3: 'authorized'}

#: Statuses under which the camera cannot be opened
DENIED_STATUSES = ('denied', 'restricted')

_AVFOUNDATION = '/System/Library/Frameworks/AVFoundation.framework/AVFoundation'


def camera_authorization():
    """
    Returns:
        'authorized', 'denied', 'restricted', 'not_determined' or 'unknown'
    """
    if platform.system() != 'Darwin':
        return 'unknown'
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
        avfoundation = ctypes.cdll.LoadLibrary(_AVFOUNDATION)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        # NSInteger objc_msgSend(id, SEL, id)
        send = ctypes.cast(objc.objc_msgSend,
                           ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.c_void_p))
        media_type = ctypes.c_void_p.in_dll(avfoundation, 'AVMediaTypeVideo')
        status = send(objc.objc_getClass(b'AVCaptureDevice'),
                      objc.sel_registerName(b'authorizationStatusForMediaType:'),
                      media_type)
        return _STATUS_NAMES.get(status, 'unknown')
    except Exception:
        return 'unknown'


def camera_permission_denied():
    """True when macOS has the camera blocked for this app (denied or restricted)"""
    return camera_authorization() in DENIED_STATUSES
