#!/usr/bin/env python3
"""
macOS Application Policy Module

The engine runs as a subprocess of the same bundle executable. The moment
cv2.imshow creates its first HighGUI window, AppKit registers the process as
a regular app and macOS gives it a second Dock tile identical to the
launcher's. Switching the process to the Accessory activation policy BEFORE
that first window exists keeps it out of the Dock entirely while its windows
still show and can take key focus when clicked.

LSUIElement in Info.plist can't be used instead - the launcher shares the
same plist and would disappear from the Dock too.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import ctypes
import ctypes.util
import platform

NS_ACTIVATION_POLICY_ACCESSORY = 1


def set_accessory_policy() -> bool:
    """
    Keep this process out of the Dock while still allowing windows.

    Creates the shared NSApplication (HighGUI reuses it later) and sets its
    activation policy to Accessory. Safe no-op off macOS or on any failure.

    Returns:
        True if the policy was applied
    """
    if platform.system() != 'Darwin':
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        # id objc_msgSend(id, SEL) - [NSApplication sharedApplication]
        send_id = ctypes.cast(objc.objc_msgSend,
                              ctypes.CFUNCTYPE(ctypes.c_void_p,
                                               ctypes.c_void_p, ctypes.c_void_p))
        app = send_id(objc.objc_getClass(b'NSApplication'),
                      objc.sel_registerName(b'sharedApplication'))
        if not app:
            return False

        # BOOL objc_msgSend(id, SEL, NSInteger) - [app setActivationPolicy:]
        send_policy = ctypes.cast(objc.objc_msgSend,
                                  ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                                   ctypes.c_void_p, ctypes.c_long))
        return bool(send_policy(app, objc.sel_registerName(b'setActivationPolicy:'),
                                NS_ACTIVATION_POLICY_ACCESSORY))
    except Exception:
        return False
