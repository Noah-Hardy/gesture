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
moment, so the fix is to reassert Accessory after every imshow/waitKey pair
- see reassert_accessory_policy() below.

An earlier version of this module budgeted only a fixed number of reassert
calls (10) before giving up permanently, on the assumption HighGUI's policy
reset always lands within the first handful of frames. It doesn't always:
window creation is async relative to imshow()/waitKey() returning, and slow
first-frame model warmup can delay reaching any fixed frame count past when
the reset actually happens. Once that budget ran out before the reset did,
the second Dock icon stuck around for the rest of the session with no
further defense - reported as #43, a "regression" of this same fix with no
code changes in between. reassert_accessory_policy() now reasserts
unconditionally, for the life of the process, which only stays cheap
because _objc_state() below caches the objc plumbing instead of redoing it
every call.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import ctypes
import ctypes.util
import platform

NS_ACTIVATION_POLICY_ACCESSORY = 1

# Populated once, lazily, by _objc_state(): None before the first attempt,
# False after a failed attempt (so later calls fail fast instead of
# repeating a doomed ctypes.cdll.LoadLibrary), or an (app, send_policy, sel)
# tuple once objc is loaded and NSApplication is reachable.
_state = None


def _objc_state():
    """
    Lazily load libobjc and resolve everything needed to set the shared
    NSApplication's activation policy, caching the result at module scope.

    Doing this once - instead of on every call - is what makes it cheap
    enough for reassert_accessory_policy() to call unconditionally every
    frame rather than budgeting a fixed number of attempts and giving up:
    the objc_msgSend call this enables is a few hundred nanoseconds, but
    ctypes.cdll.LoadLibrary plus resolving objc_getClass/sel_registerName
    is not something worth paying per frame.

    Returns:
        (app, send_policy, sel) tuple, or None if unavailable (off macOS,
        or objc/NSApplication could not be reached).
    """
    global _state
    if _state is not None:
        return _state or None
    if platform.system() != 'Darwin':
        _state = False
        return None
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
            _state = False
            return None

        # BOOL objc_msgSend(id, SEL, NSInteger) - [app setActivationPolicy:]
        send_policy = ctypes.cast(objc.objc_msgSend,
                                  ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                                   ctypes.c_void_p, ctypes.c_long))
        sel = objc.sel_registerName(b'setActivationPolicy:')
        _state = (app, send_policy, sel)
        return _state
    except Exception:
        _state = False
        return None


def _apply_accessory_policy() -> bool:
    """
    Set the shared NSApplication's activation policy to Accessory.

    Safe no-op off macOS or on any failure (including a first-call failure
    to reach objc, which _objc_state() caches so later calls fail fast
    instead of repeating the same doomed load attempt).

    Returns:
        True if the policy was applied
    """
    state = _objc_state()
    if state is None:
        return False
    app, send_policy, sel = state
    try:
        return bool(send_policy(app, sel, NS_ACTIVATION_POLICY_ACCESSORY))
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
    every cv2.waitKey() that follows the first cv2.imshow() wins that race
    back - unconditionally, for the life of the process, not just the
    first few frames (see the module docstring for why a fixed call budget
    used to leave the Dock icon stuck once HighGUI's reset landed later
    than expected). The objc_msgSend this makes is a few hundred
    nanoseconds thanks to _objc_state()'s caching, versus milliseconds of
    MediaPipe inference happening in the same loop iteration, so paying it
    every frame is not a meaningful cost.

    Returns:
        True if the policy was (re)applied
    """
    return _apply_accessory_policy()
