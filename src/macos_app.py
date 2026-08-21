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

HighGUI itself undoes this: the first time it actually creates a window
(inside cv2.imshow), Cocoa's window-creation path re-registers the app as a
Regular application, popping the second Dock tile right back up even though
we set Accessory before any window existed. There's no hook into that
moment, so the fix is to reassert Accessory immediately after the first
imshow/waitKey pair - see reassert_accessory_policy() below.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import ctypes
import ctypes.util
import platform

NS_ACTIVATION_POLICY_ACCESSORY = 1

# reassert_accessory_policy() only needs to win the race against HighGUI's
# first window a handful of times - once the policy has stuck, further
# calls are wasted objc round-trips for the rest of the (possibly
# long-running) session. Cap them.
_MAX_REASSERT_CALLS = 10
_reassert_call_count = 0


def _apply_accessory_policy() -> bool:
    """
    Shared ctypes/objc plumbing behind both set_accessory_policy() and
    reassert_accessory_policy().

    Gets (or creates) the shared NSApplication and sets its activation
    policy to Accessory. Safe no-op off macOS or on any failure.

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


def set_accessory_policy() -> bool:
    """
    Keep this process out of the Dock while still allowing windows.

    Creates the shared NSApplication (HighGUI reuses it later) and sets its
    activation policy to Accessory. Safe no-op off macOS or on any failure.

    Returns:
        True if the policy was applied
    """
    return _apply_accessory_policy()


def reassert_accessory_policy() -> bool:
    """
    Re-apply the Accessory policy after HighGUI has created its window.

    HighGUI's Cocoa backend flips the app back to the Regular activation
    policy the moment it creates its first window (inside cv2.imshow),
    which is what makes the second Dock tile reappear even though
    set_accessory_policy() already ran at startup. Calling this right after
    the first cv2.waitKey() that follows the first cv2.imshow() wins that
    race back. Safe no-op off macOS, on any failure, or once it has already
    been called _MAX_REASSERT_CALLS times (no need to keep paying for an
    objc round-trip every frame for the life of the process).

    Returns:
        True if the policy was (re)applied
    """
    global _reassert_call_count
    if platform.system() != 'Darwin':
        return False
    if _reassert_call_count >= _MAX_REASSERT_CALLS:
        return False
    _reassert_call_count += 1
    return _apply_accessory_policy()
