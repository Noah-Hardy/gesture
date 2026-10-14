#!/usr/bin/env python3
"""
Launch Animation

A skippable ~7.5 second splash that plays between "the engine process is up"
and "the main window is ready": a camera senses a body, landmarks lock on
joint by joint, the figure dances, lands in the icon pose, and the splash
expands into the main window while the icon flies into the rail.

Ported from the Claude Design handoff (mp-osc-launch.jsx on animations-v3):
every keyframe, easing and timing below is the design's own value. The
choreography is pure functions of authored time T (no Qt), so it is unit
testable; the painting lives further down and only reads those functions.

Run standalone with `python -m src.ui.launch`; `--frames DIR` renders still
frames offscreen, `--bench` measures paint time.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import time
from typing import Callable

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QPixmap, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
    QPen, QRadialGradient, QBrush, QTransform,
)
from PySide6.QtWidgets import QApplication, QWidget

from .tokens import PALETTE, Font, color

# ============================================================================
# CANVAS AND CUES
# ============================================================================
CANVAS_W, CANVAS_H = 1920, 1080

# Gesture Launch.dc.html: window.OM_SCENES (name, duration in seconds)
SCENES = (
    ('Sensing', 1.2),
    ('Locking', 1.0),
    ('Dance', 1.4),
    ('Collapse', 1.4),
    ('Reveal', 2.5),
)

# Input in the first moments is ignored: a launch from Finder/Dock can deliver
# a stray click or key to the freshly shown splash and would skip it at once
SKIP_GRACE_MS = 700

# Cross-fade from the splash to the (identical) real window at the end
HANDOVER_MS = 140

# The squircle's authored size on the canvas, and the rail mark's real size in px
ICON_SIZE = 340
RAIL_MARK_PX = 26

# The mock window's rect on the authored canvas (AppWindow in the design)
WIN_X, WIN_Y, WIN_W, WIN_H = 180, 100, 1560, 880

# Real startup log lines (src/runtime.py), shown in the mock console
_FALLBACK_LOG = ('Opening camera…', 'Loading model…', 'Warming up…', 'Ready')

# Real addresses the default `float` format sends in "all" mode (src/osc_protocol.py)
ADDRS = (
    '/pose/lm/0',
    '/pose/world/lm/0',
    '/left_hand/lm/8',
    '/right_hand/lm/8',
    '/gesture/pose/tracking',
    '/gesture/hand/tracking',
)


def derive_cues(scenes=SCENES) -> tuple[dict[str, float], float]:
    """
    Cue table from the scene list (animations-v3 ccDerive).

    Args:
        scenes: (name, duration) pairs in order.

    Returns:
        ({name: authored start}, authored total). A duplicate name binds to
        its first occurrence, as in the design engine.
    """
    table: dict[str, float] = {}
    start = 0.0
    for name, dur in scenes:
        if name not in table:
            table[name] = round(start * 1000) / 1000
        start += dur
    return table, round(start * 1000) / 1000


CUES, TOTAL = derive_cues()


# ============================================================================
# EASING AND TIMELINE HELPERS (animations-v3.jsx)
# ============================================================================
def _ease_out_back(t: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _ease_in_out_back(t: float) -> float:
    c1 = 1.70158
    c2 = c1 * 1.525
    if t < 0.5:
        return ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2
    return ((2 * t - 2) ** 2 * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2


def _ease_out_elastic(t: float) -> float:
    c4 = (2 * math.pi) / 3
    if t == 0:
        return 0.0
    if t == 1:
        return 1.0
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * c4) + 1


def _ease_in_out_expo(t: float) -> float:
    if t == 0:
        return 0.0
    if t == 1:
        return 1.0
    if t < 0.5:
        return 0.5 * 2 ** (20 * t - 10)
    return 1 - 0.5 * 2 ** (-20 * t + 10)


EASING: dict[str, Callable[[float], float]] = {
    'linear': lambda t: t,
    'easeInQuad': lambda t: t * t,
    'easeOutQuad': lambda t: t * (2 - t),
    'easeInOutQuad': lambda t: 2 * t * t if t < 0.5 else -1 + (4 - 2 * t) * t,
    'easeInCubic': lambda t: t * t * t,
    'easeOutCubic': lambda t: (t - 1) ** 3 + 1,
    'easeInOutCubic': lambda t: 4 * t * t * t if t < 0.5 else (t - 1) * (2 * t - 2) * (2 * t - 2) + 1,
    'easeInQuart': lambda t: t ** 4,
    'easeOutQuart': lambda t: 1 - (t - 1) ** 4,
    'easeInOutQuart': lambda t: 8 * t ** 4 if t < 0.5 else 1 - 8 * (t - 1) ** 4,
    'easeInExpo': lambda t: 0.0 if t == 0 else 2 ** (10 * (t - 1)),
    'easeOutExpo': lambda t: 1.0 if t == 1 else 1 - 2 ** (-10 * t),
    'easeInOutExpo': _ease_in_out_expo,
    'easeInSine': lambda t: 1 - math.cos((t * math.pi) / 2),
    'easeOutSine': lambda t: math.sin((t * math.pi) / 2),
    'easeInOutSine': lambda t: -(math.cos(math.pi * t) - 1) / 2,
    'easeOutBack': _ease_out_back,
    'easeInBack': lambda t: (1.70158 + 1) * t * t * t - 1.70158 * t * t,
    'easeInOutBack': _ease_in_out_back,
    'easeOutElastic': _ease_out_elastic,
}


def clamp(v: float, lo: float, hi: float) -> float:
    """Clamp `v` to [lo, hi]."""
    return max(lo, min(hi, v))


def animate(frm: float = 0.0, to: float = 1.0, start: float = 0.0, end: float = 1.0,
            ease: Callable[[float], float] = EASING['easeInOutCubic']) -> Callable[[float], float]:
    """
    Single-segment tween (animations-v3 `animate`).

    Args:
        frm: Value at and before `start`.
        to: Value at and after `end`.
        start: Authored time the tween begins.
        end: Authored time the tween ends.
        ease: Easing applied to the 0..1 local progress.

    Returns:
        fn(T) -> value.
    """
    def fn(t: float) -> float:
        if t <= start:
            return frm
        if t >= end:
            return to
        return frm + (to - frm) * ease((t - start) / (end - start))
    return fn


# MOTION: the three helpers the piece is allowed to use
def enter(frm, to, start, end):
    """Decelerating arrival (easeOutCubic)."""
    return animate(frm, to, start, end, EASING['easeOutCubic'])


def draw(frm, to, start, end):
    """Symmetric draw / fade (easeInOutQuad)."""
    return animate(frm, to, start, end, EASING['easeInOutQuad'])


def pop(frm, to, start, end):
    """Overshooting pop (easeOutBack)."""
    return animate(frm, to, start, end, EASING['easeOutBack'])


def lerp(a: float, b: float, u: float) -> float:
    """Linear interpolation."""
    return a + (b - a) * u


# ============================================================================
# KEYFRAMES
# ============================================================================
JOINTS = ('head', 'chest', 'hip', 'ls', 'rs', 'le', 're', 'lh', 'rh', 'lk', 'rk', 'lf', 'rf')

POSE_IDLE = {
    'head': (0.50, 0.15), 'chest': (0.50, 0.34), 'hip': (0.50, 0.56),
    'ls': (0.40, 0.32), 'rs': (0.60, 0.32), 'le': (0.34, 0.45), 're': (0.66, 0.45),
    'lh': (0.31, 0.58), 'rh': (0.69, 0.58), 'lk': (0.45, 0.74), 'rk': (0.55, 0.74),
    'lf': (0.43, 0.94), 'rf': (0.57, 0.94),
}
POSE_A = {
    'head': (0.47, 0.14), 'chest': (0.48, 0.33), 'hip': (0.50, 0.55),
    'ls': (0.38, 0.31), 'rs': (0.59, 0.32), 'le': (0.28, 0.22), 're': (0.70, 0.43),
    'lh': (0.22, 0.10), 'rh': (0.82, 0.33), 'lk': (0.42, 0.73), 'rk': (0.60, 0.69),
    'lf': (0.36, 0.93), 'rf': (0.73, 0.87),
}
POSE_B = {
    'head': (0.54, 0.16), 'chest': (0.52, 0.35), 'hip': (0.50, 0.56),
    'ls': (0.43, 0.32), 'rs': (0.63, 0.33), 'le': (0.33, 0.46), 're': (0.74, 0.26),
    'lh': (0.27, 0.60), 'rh': (0.80, 0.12), 'lk': (0.40, 0.71), 'rk': (0.58, 0.75),
    'lf': (0.30, 0.89), 'rf': (0.62, 0.96),
}
# The icon's pose: one asymmetric stance inside the measuring circle
POSE_ICON = {
    'head': (0.500, 0.260), 'chest': (0.500, 0.432), 'hip': (0.500, 0.576),
    'ls': (0.404, 0.418), 'rs': (0.596, 0.418), 'le': (0.280, 0.356), 're': (0.720, 0.460),
    'lh': (0.197, 0.239), 'rh': (0.830, 0.466), 'lk': (0.417, 0.734), 'rk': (0.617, 0.721),
    'lf': (0.397, 0.900), 'rf': (0.727, 0.872),
}

BONES = (
    ('head', 'chest'), ('chest', 'hip'),
    ('chest', 'ls'), ('ls', 'le'), ('le', 'lh'),
    ('chest', 'rs'), ('rs', 're'), ('re', 'rh'),
    ('hip', 'lk'), ('lk', 'lf'), ('hip', 'rk'), ('rk', 'rf'),
)

NODE_R = {'head': 26, 'chest': 15, 'hip': 15, 'ls': 10, 'rs': 10, 'le': 10, 're': 10,
          'lh': 17, 'rh': 17, 'lk': 10, 'rk': 10, 'lf': 17, 'rf': 17}

NOISE = tuple(
    {'x': 0.5 + 0.42 * math.sin(i * 2.399), 'y': 0.08 + 0.86 * ((i * 0.379) % 1),
     'ph': (i * 0.71) % 1, 'r': 2 + (i % 3)}
    for i in range(34)
)


# ============================================================================
# CHOREOGRAPHY (pure functions of authored time)
# ============================================================================
def blend(p0: dict, p1: dict, u: float) -> dict:
    """Joint-wise interpolation between two poses."""
    return {j: (lerp(p0[j][0], p1[j][0], u), lerp(p0[j][1], p1[j][1], u)) for j in JOINTS}


def sine_u(u: float) -> float:
    """Cosine ease on 0..1 (the dance's in/out)."""
    return 0.5 - 0.5 * math.cos(math.pi * clamp(u, 0, 1))


def pose_at(t: float, cues: dict | None = None) -> dict:
    """
    The figure's pose at authored time `t`: idle, then A, B, and into the icon pose.

    Args:
        t: Authored seconds.
        cues: Cue table; defaults to CUES.

    Returns:
        {joint: (x, y)} in 0..1 figure space.
    """
    cues = cues or CUES
    dance_len = cues['Collapse'] - cues['Dance']
    if t <= cues['Dance']:
        return POSE_IDLE
    f = clamp((t - cues['Dance']) / dance_len, 0, 1)
    if f < 0.28:
        return blend(POSE_IDLE, POSE_A, sine_u(f / 0.28))
    if f < 0.62:
        return blend(POSE_A, POSE_B, sine_u((f - 0.28) / 0.34))
    return blend(POSE_B, POSE_ICON, sine_u((f - 0.62) / 0.38))


def live_pose(t: float) -> dict:
    """The figure keeps dancing inside the mock preview once the UI is up."""
    f = (t * 0.62) % 1
    if f < 0.5:
        return blend(POSE_A, POSE_B, sine_u(f / 0.5))
    return blend(POSE_B, POSE_A, sine_u((f - 0.5) / 0.5))


def lock_time(joint: str, cues: dict | None = None) -> float:
    """Authored time the joint locks: head at Sensing +0.45, then 0.105s apart."""
    cues = cues or CUES
    return cues['Sensing'] + 0.45 + JOINTS.index(joint) * 0.105


def figure_state(t: float) -> dict:
    """
    Figure placement: width, anchor, scale and bob at `t`.

    Returns:
        dict with fw, anchor_y, fig_scale, cy, bob (canvas px / ratios).
    """
    c = CUES
    fw = draw(620, 700, c['Collapse'], c['Collapse'] + 0.5)(t)
    anchor_y = draw(0.52, 0.535, c['Collapse'], c['Collapse'] + 0.5)(t)
    if t < c['Dance']:
        cam = draw(1.10, 1.0, c['Sensing'], c['Dance'])(t)
    else:
        cam = draw(1.0, 1.04, c['Dance'], c['Collapse'])(t)
    collapse_s = draw(1, 0.385, c['Collapse'], c['Collapse'] + 0.55)(t)
    cy = draw(556, 452, c['Collapse'], c['Collapse'] + 0.55)(t)
    bob = math.sin((t - c['Dance']) * 7.2) * 7 if c['Dance'] < t < c['Collapse'] else 0.0
    return {'fw': fw, 'anchor_y': anchor_y, 'fig_scale': cam * collapse_s, 'cy': cy, 'bob': bob}


def node_state(t: float, joint: str) -> dict:
    """
    One joint's lock animation.

    Returns:
        dict with a (pop progress, overshoots), opacity, flash (bool), dx/dy
        (entry offset px) and radius multiplier.
    """
    i = JOINTS.index(joint)
    lock = lock_time(joint)
    a = pop(0, 1, lock, lock + 0.3)(t)
    off = (1 - clamp(a, 0, 1)) * 30
    return {
        'a': a,
        'opacity': clamp(a * 1.2, 0, 1),
        'flash': lock < t < lock + 0.16,
        'dx': math.cos(i * 2.4) * off,
        'dy': math.sin(i * 2.4) * off,
        'radius': max(0.1, NODE_R[joint] * clamp(a, 0, 1.15)),
    }


def bone_progress(t: float, a: str, b: str) -> float:
    """Dash-reveal progress of the bone a-b (0 until both joints have locked)."""
    start = max(lock_time(a), lock_time(b)) + 0.04
    return draw(0, 1, start, start + 0.3)(t)


def chrome_state(t: float) -> dict:
    """Viewfinder alphas: hud, sweep (+y), noise, stream."""
    c = CUES
    hud = enter(0, 1, 0, 0.5)(t) * draw(1, 0, c['Collapse'], c['Collapse'] + 0.3)(t)
    return {
        'hud': hud,
        'sweep_y': ((t * 0.42) % 1) * CANVAS_H,
        'sweep_on': draw(1, 0, c['Dance'] - 0.2, c['Dance'] + 0.3)(t),
        'noise_on': draw(1, 0, c['Sensing'] + 0.9, c['Locking'] + 0.6)(t),
        'stream_on': draw(0, 1, c['Dance'] - 0.1, c['Dance'] + 0.5)(t)
        * draw(1, 0, c['Collapse'], c['Collapse'] + 0.25)(t),
    }


def circle_progress(t: float) -> float:
    """Progress of the single inscribing circle."""
    return draw(0, 1, CUES['Collapse'] + 0.25, CUES['Collapse'] + 0.8)(t)


def icon_state(t: float, fly_to: tuple[float, float, float] | None = None) -> dict:
    """
    Squircle, wordmark and flight-to-rail values.

    Args:
        t: Authored seconds.
        fly_to: (x, y, scale) the flight ends at, in canvas units; defaults to
            the mock window's rail slot.
    """
    c = CUES
    tx, ty, ts = fly_to if fly_to is not None else (216, 225, 0.105)
    word_a = enter(0, 1, c['Collapse'] + 0.4, c['Collapse'] + 0.85)(t) \
        * draw(1, 0, c['Reveal'], c['Reveal'] + 0.28)(t)
    flight = draw(0, 1, c['Reveal'] + 0.42, c['Reveal'] + 1.25)(t)
    return {
        'sq_a': enter(0, 1, c['Collapse'] + 0.12, c['Collapse'] + 0.55)(t),
        'sq_s': pop(0.72, 1, c['Collapse'] + 0.12, c['Collapse'] + 0.62)(t),
        'word_a': word_a,
        'word_y': enter(22, 0, c['Collapse'] + 0.4, c['Collapse'] + 0.9)(t),
        'fly_x': lerp(960, tx, flight),
        'fly_y': lerp(452, ty, flight),
        'fly_s': lerp(1, ts, flight),
    }


def window_state(t: float) -> dict:
    """The mock window's pane-by-pane reveal alphas (shell, rail, stage, meters, osc, cons)."""
    r = CUES['Reveal']

    def at(a, b):
        return enter(0, 1, r + a, r + b)(t)

    return {
        'shell': at(0.05, 0.5),
        'shell_s': enter(0.9, 1, r + 0.05, r + 0.55)(t),
        'rail': at(0.35, 0.75),
        'stage': at(0.4, 0.85),
        'meters': at(0.5, 0.95),
        'osc': at(0.55, 1.0),
        'cons': at(0.62, 1.05),
    }


# ============================================================================
# REDUCE MOTION AND PLAY RULES
# ============================================================================
def reduce_motion_enabled() -> bool:
    """
    Whether macOS "Reduce motion" is on. False off-macOS and on any failure.

    Asks NSWorkspace.accessibilityDisplayShouldReduceMotion through the
    Objective-C runtime (no PyObjC), falling back to the universalaccess
    default.
    """
    if sys.platform != 'darwin':
        return False
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
        ctypes.cdll.LoadLibrary('/System/Library/Frameworks/AppKit.framework/AppKit')
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send_obj = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(('objc_msgSend', objc))
        send_bool = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(('objc_msgSend', objc))
        ws = send_obj(objc.objc_getClass(b'NSWorkspace'), objc.sel_registerName(b'sharedWorkspace'))
        if ws:
            return bool(send_bool(ws, objc.sel_registerName(b'accessibilityDisplayShouldReduceMotion')))
    except Exception:
        pass
    try:
        out = subprocess.run(['defaults', 'read', 'com.apple.universalaccess', 'reduceMotion'],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        return out in ('1', 'true', 'YES')
    except Exception:
        return False


def _console_lines() -> tuple[str, ...]:
    """The engine's real startup log lines (src/runtime.py), for the mock console."""
    try:
        from .. import runtime
        t = runtime._PHASE_TEXT
        return tuple(t[p] for p in runtime.PHASES)
    except Exception:
        return _FALLBACK_LOG


_version_cache: str | None = None


def _app_version_cached() -> str:
    global _version_cache
    if _version_cache is None:
        _version_cache = _app_version()
    return _version_cache


def _app_version() -> str:
    try:
        from .. import docs
        return docs.app_version() or ''
    except Exception:
        return ''


# ============================================================================
# PAINTING
# ============================================================================
_ACCENT = PALETTE['accent']
_DIM = PALETTE['accent_2']
_TEXT = PALETTE['text']
_MUTE = PALETTE['dim']
_FAINT = PALETTE['faint']
_BORDER = PALETTE['border']

_fonts: dict = {}


def _font(kind: str, px: float, weight: int = 400, spacing: float = 0.0) -> QFont:
    """Cached UI/mono font; `spacing` is absolute letter spacing in px."""
    key = (kind, px, weight, spacing)
    f = _fonts.get(key)
    if f is None:
        f = Font.mono(px, weight) if kind == 'mono' else Font.ui(px, weight)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        _fonts[key] = f
    return f


def _qc(name: str, alpha: float = 1.0) -> QColor:
    return color(name, clamp(alpha, 0, 1))


def _text(p: QPainter, s: str, x: float, yc: float, font: QFont, col: QColor, align: str = 'l') -> float:
    """Draw `s` vertically centred on `yc`; align 'l' (x = left), 'r' (x = right) or 'c'. Returns advance."""
    fm = QFontMetricsF(font)
    w = fm.horizontalAdvance(s)
    left = x if align == 'l' else (x - w if align == 'r' else x - w / 2)
    p.setFont(font)
    p.setPen(col)
    p.drawText(QPointF(left, yc + (fm.ascent() - fm.descent()) / 2), s)
    return w


def _rrect(p: QPainter, x, y, w, h, r, fill=None, border=None, bw=1.0) -> None:
    if fill is None:
        p.setBrush(Qt.BrushStyle.NoBrush)
    else:
        p.setBrush(fill)
    if border is None:
        p.setPen(Qt.PenStyle.NoPen)
    else:
        p.setPen(QPen(border, bw))
        x, y, w, h = x + bw / 2, y + bw / 2, w - bw, h - bw
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _vgrad(y0: float, y1: float, c0: QColor, c1: QColor) -> QLinearGradient:
    g = QLinearGradient(0, y0, 0, y1)
    g.setColorAt(0, c0)
    g.setColorAt(1, c1)
    return g


def _paint_mini_skeleton(p: QPainter, t: float, rect: QRectF) -> None:
    """The live figure inside the mock preview (viewBox 100x100, meet)."""
    pose = live_pose(t)
    s = min(rect.width(), rect.height()) / 100
    ox = rect.x() + (rect.width() - 100 * s) / 2
    oy = rect.y() + (rect.height() - 100 * s) / 2

    def pt(v):
        return QPointF(ox + (18 + v[0] * 64) * s, oy + (6 + v[1] * 88) * s)

    pen = QPen(_qc('accent_2'), 1.5 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for a, b in BONES:
        p.drawLine(pt(pose[a]), pt(pose[b]))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_qc('accent'))
    for j in JOINTS:
        p.drawEllipse(pt(pose[j]), NODE_R[j] / 11 * s, NODE_R[j] / 11 * s)


def _paint_app_window(p: QPainter, t: float, version: str, log_lines: tuple) -> None:
    """The mock main window, expanding pane by pane (AppWindow in the design)."""
    w = window_state(t)
    if w['shell'] <= 0.001:
        return
    W, H = WIN_W, WIN_H
    p.save()
    # transformOrigin 50% 42%, scale shell_s
    p.translate(WIN_X + W * 0.5, WIN_Y + H * 0.42)
    p.scale(w['shell_s'], w['shell_s'])
    p.translate(-W * 0.5, -H * 0.42)
    p.setOpacity(w['shell'])

    # shadow: 0 60px 120px rgba(0,0,0,.65), approximated by stacked rects
    p.setPen(Qt.PenStyle.NoPen)
    for k in range(5):
        grow = 10 + k * 20
        p.setBrush(QColor(0, 0, 0, 32))
        p.drawRoundedRect(QRectF(-grow, 60 - grow, W + 2 * grow, H + 2 * grow), 20 + grow, 20 + grow)

    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, W, H), 20, 20)
    p.setClipPath(clip)
    p.fillRect(QRectF(0, 0, W, H), QColor(PALETTE['bg_window']))
    p.setClipping(True)

    body_top, console_h = 76, 164
    body_h = H - body_top - console_h

    # ---- toolbar
    p.fillRect(QRectF(0, 0, W, 76), _vgrad(0, 76, QColor('#22262f'), QColor('#1E2129')))
    p.fillRect(QRectF(0, 75, W, 1), _qc('border'))
    for i, c in enumerate(('#FF5F57', '#FEBC2E', '#28C840')):
        p.setBrush(QColor(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(24 + 9 + i * 30, 38), 9, 9)
    div = QLinearGradient(0, 22, 0, 54)
    div.setColorAt(0, QColor(0, 0, 0, 0))
    div.setColorAt(0.5, _qc('border_2'))
    div.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(224, 22, 1, 32), div)

    p.save()
    p.setOpacity(w['shell'] * w['rail'])
    err = _qc('error')
    pill_x = 247
    _rrect(p, pill_x, 16, 106, 44, 11, _qc('error', 0.078), err)
    for g, a in ((14, 0.12), (8, 0.2), (4, 0.3)):
        p.setBrush(_qc('error', a))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(pill_x + 22 + 5.5, 38), 5.5 + g / 2, 5.5 + g / 2)
    p.setBrush(err)
    p.drawEllipse(QPointF(pill_x + 22 + 5.5, 38), 5.5, 5.5)
    _text(p, 'Stop', pill_x + 22 + 11 + 13, 38, _font('ui', 19, 500), err)
    x = pill_x + 106 + 22
    lab, val = _font('ui', 17), _font('ui', 17, 500)
    for k, v in (('MODE ', 'all'), ('IN ', 'Camera 0'), ('OUT ', '127.0.0.1:1234')):
        x += _text(p, k, x, 38, lab, _qc('faint'))
        x += _text(p, v, x, 38, val, _qc('text')) + 26
    p.restore()

    p.save()
    p.setOpacity(w['shell'] * w['meters'])
    _text(p, f'{29 + int(t * 3) % 2} fps · 12 ms', W - 24, 38, _font('ui', 17), _qc('dim'), 'r')
    p.restore()

    # ---- rail
    p.fillRect(QRectF(0, body_top, 250, body_h), QColor(PALETTE['rail']))
    p.fillRect(QRectF(250, body_top, 1, body_h), _qc('border'))
    p.save()
    p.setOpacity(w['shell'] * w['rail'])
    _text(p, 'Gesture', 70, body_top + 18 + 31 - 10, _font('ui', 18, 500), _qc('text'))
    _text(p, version, 70, body_top + 18 + 31 + 11, _font('ui', 14), _qc('faint'))
    rise = (1 - w['rail']) * 16
    for i, name in enumerate(('Live', 'Input', 'OSC Output', 'Tracking', 'Preview', 'Advanced', 'Help')):
        y = body_top + 18 + 62 + 16 + i * 48 + rise
        if i == 0:
            _rrect(p, 14, y, 222, 44, 11, QColor('#232830'))
            _rrect(p, 26, y + 12.5, 4, 19, 2, _qc('accent'))
        _text(p, name, 43, y + 22, _font('ui', 18), _qc('text' if i == 0 else 'dim'))
    p.restore()

    # ---- live pane: stage + meters
    px0, pw = 275, 860
    meters_h = 105
    stage_h = body_h - 48 - 18 - meters_h
    sy = body_top + 24
    p.save()
    p.setOpacity(w['shell'] * w['stage'])
    p.translate(0, (1 - w['stage']) * 16)
    stage = QRectF(px0, sy, pw, stage_h)
    rg = QRadialGradient(QPointF(stage.center().x(), stage.top()), stage.width() * 1.2)
    rg.setColorAt(0, QColor(PALETTE['stage_end']))
    rg.setColorAt(1, QColor(PALETTE['stage']))
    sp = QPainterPath()
    sp.addRoundedRect(stage, 14, 14)
    # CSS ellipse radii (1.2w x 1.0h): squash the circular gradient with a brush transform
    brush = QBrush(rg)
    tr = QTransform()
    tr.translate(stage.center().x(), stage.top())
    tr.scale(1, stage.height() / (stage.width() * 1.2))
    tr.translate(-stage.center().x(), -stage.top())
    brush.setTransform(tr)
    _rrect(p, stage.x(), stage.y(), stage.width(), stage.height(), 14, brush, _qc('border'))
    p.save()
    p.setClipPath(sp)
    _paint_mini_skeleton(p, t, stage)
    p.restore()
    chip = _font('ui', 17)
    cx = stage.x() + 18
    for label, col in (('Camera 0 · 640×480 · 30 fps', _qc('dim')), ('holistic', _qc('accent'))):
        tw = QFontMetricsF(chip).horizontalAdvance(label)
        _rrect(p, cx, stage.y() + 16, tw + 24, 36, 9, QColor(13, 15, 19, 0x99), _qc('border'))
        _text(p, label, cx + 12, stage.y() + 34, chip, col)
        cx += tw + 24 + 10
    _text(p, 'preview only — not what is sent', stage.right() - 18, stage.bottom() - 16 - 10,
          _font('ui', 16), _qc('faint'), 'r')
    p.restore()

    p.save()
    p.setOpacity(w['shell'] * w['meters'])
    p.translate(0, (1 - w['meters']) * 16)
    cw = (pw - 42) / 4
    my = sy + stage_h + 18
    cards = (('Frame rate', '29', 'fps', 0.93), ('Landmarks sent', '75', 'pts/frame', 0.78),
             ('Detections', '1 + 2', 'pose · hands', 1.0), ('Send latency', '12', 'ms', 0.22))
    for i, (lbl, val, unit, frac) in enumerate(cards):
        cx = px0 + i * (cw + 14)
        _rrect(p, cx, my, cw, meters_h, 14, _vgrad(my, my + meters_h, _qc('panel'), _qc('panel_end')), _qc('border'))
        _text(p, lbl.upper(), cx + 16, my + 14 + 8.5, _font('ui', 14, 400, 1.7), _qc('faint'))
        vw = _text(p, val, cx + 16, my + 14 + 17 + 10 + 18, _font('ui', 30, 500), _qc('text'))
        _text(p, unit, cx + 16 + vw + 7, my + 14 + 17 + 10 + 22, _font('ui', 15), _qc('dim'))
        ty = my + meters_h - 14 - 4
        _rrect(p, cx + 16, ty, cw - 32, 4, 2, _qc('border'))
        _rrect(p, cx + 16, ty, (cw - 32) * frac, 4, 2, _qc('accent'))
    p.restore()

    # ---- OSC activity
    ox = W - 400
    p.save()
    p.setOpacity(w['shell'] * w['osc'])
    p.translate(0, (1 - w['osc']) * 16)
    p.fillRect(QRectF(ox, body_top, 400, body_h), QColor(PALETTE['rail']))
    p.fillRect(QRectF(ox, body_top, 1, body_h), _qc('border'))
    _text(p, 'OSC ACTIVITY', ox + 16 + 8, body_top + 20 + 9, _font('ui', 15, 400, 1.5), _qc('dim'))
    _text(p, f'{176 + int(t * 5) % 7} msg/s', ox + 400 - 16 - 8, body_top + 20 + 9,
          _font('ui', 15), _qc('accent'), 'r')
    for i, a in enumerate(ADDRS):
        ry = body_top + 20 + 26 + 8 + i * 76
        if i % 2 == 0:
            _rrect(p, ox + 16, ry, 367, 68, 11, _qc('row_alt'))
        dot = 0.4 + 0.6 * abs(math.sin(t * 6 + i))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc('accent', dot))
        p.drawEllipse(QPointF(ox + 16 + 12 + 3.5, ry + 11 + 9), 3.5, 3.5)
        _text(p, a, ox + 16 + 12 + 7 + 10, ry + 11 + 9, _font('mono', 15), _qc('text'))
        _text(p, '29/s', ox + 16 + 367 - 12, ry + 11 + 9, _font('ui', 14), _qc('faint'), 'r')
        by = ry + 11 + 18 + 8 + 20
        for k in range(20):
            v = 0.25 + 0.75 * abs(math.sin(t * 3 + k * 0.8 + i))
            p.setBrush(_qc('accent', 0.3 + v * 0.5))
            p.drawRoundedRect(QRectF(ox + 16 + 12 + k * 7, by - v * 20, 4, v * 20), 1, 1)
    p.restore()

    # ---- console
    cy0 = H - console_h
    p.save()
    p.setOpacity(w['shell'] * w['cons'])
    p.fillRect(QRectF(0, cy0, W, console_h), QColor(PALETTE['console']))
    p.fillRect(QRectF(0, cy0, W, 1), _qc('border'))
    p.fillRect(QRectF(0, cy0 + 47, W, 1), _qc('hairline'))
    hw = _text(p, 'ENGINE OUTPUT', 24, cy0 + 1 + 23, _font('ui', 15, 400, 1.5), _qc('dim'))
    _text(p, 'stdout · SIGINT on stop', 24 + hw + 16, cy0 + 1 + 23, _font('ui', 15), _qc('faint'))
    for i, line in enumerate(log_lines):
        _text(p, line, 24, cy0 + 48 + 12 + i * 23 + 11.5, _font('mono', 15), _qc('text_2'))
    p.restore()

    p.setClipping(False)
    _rrect(p, 0, 0, W, H, 20, None, _qc('border'))
    p.restore()


class RevealPlan:
    """
    The real main window, captured for the Reveal cue.

    Everything is in canvas units, so the splash can paint the window exactly
    where (and at the size) the real one will appear.

    Attributes:
        pixmap: The window's rendered contents (device-pixel-ratio aware).
        rect: The window's content rect on the canvas.
        pieces: [(stage key, rect)] panes that fade in on their own cue; the
            rest of the window belongs to the shell layer.
        fly_to: (x, y, scale) where the icon lands: the rail's mark slot.
    """

    def __init__(self, pixmap: QPixmap, rect: QRectF, pieces: list, fly_to: tuple[float, float, float]):
        self.pixmap = pixmap
        self.rect = rect
        self.pieces = pieces
        self.fly_to = fly_to


def _paint_real_window(p: QPainter, t: float, plan: RevealPlan) -> None:
    """The real window, expanding pane by pane (same cues as the mock)."""
    w = window_state(t)
    if w['shell'] <= 0.001:
        return
    R = plan.rect
    p.save()
    p.translate(R.x() + R.width() * 0.5, R.y() + R.height() * 0.42)
    p.scale(w['shell_s'], w['shell_s'])
    p.translate(-R.width() * 0.5 - R.x(), -R.height() * 0.42 - R.y())
    p.setOpacity(w['shell'])
    p.setPen(Qt.PenStyle.NoPen)
    for k in range(5):
        grow = 10 + k * 20
        p.setBrush(QColor(0, 0, 0, 32))
        p.drawRoundedRect(R.adjusted(-grow, 60 - grow, grow, 60 + grow), 20 + grow, 20 + grow)
    # Shell layer: the window minus the panes that arrive later
    base = QPainterPath()
    base.addRect(R)
    for _key, r in plan.pieces:
        cut = QPainterPath()
        cut.addRect(r)
        base = base.subtracted(cut)
    p.save()
    p.setClipPath(base)
    p.fillRect(R, QColor(PALETTE['bg_window']))
    p.drawPixmap(R, plan.pixmap, QRectF(plan.pixmap.rect()))
    p.restore()
    for key, r in plan.pieces:
        a = w[key]
        if a <= 0.001:
            continue
        p.save()
        p.setOpacity(w['shell'] * a)
        p.setClipRect(r)
        if key not in ('rail',):
            p.translate(0, (1 - a) * 16)
        p.fillRect(R, QColor(PALETTE['bg_window']))
        p.drawPixmap(R, plan.pixmap, QRectF(plan.pixmap.rect()))
        p.restore()
    p.restore()


def _node_circle(p: QPainter, c: QPointF, r: float) -> None:
    p.drawEllipse(c, r, r)


def paint_frame(p: QPainter, t: float, version: str = '', log_lines: tuple | None = None,
                reveal: RevealPlan | None = None) -> None:
    """
    Paint the whole composition at authored time `t` on the 1920x1080 canvas.

    The caller sets up the painter (render hints, scale-to-fit transform).
    Everything visible derives from `t` only.

    Args:
        p: Active painter, with canvas coordinates.
        t: Authored seconds, 0..TOTAL.
        version: Version string for the mock rail.
        log_lines: Console lines for the mock window (defaults to the engine's).
        reveal: The real window to expand into; None paints the mock window.
    """
    W, H = CANVAS_W, CANVAS_H
    accent = _qc('accent')
    p.setPen(Qt.PenStyle.NoPen)

    # background: radial-gradient(1400px 900px at 50% 34%, #1E2129 0%, #0E1014 68%)
    p.fillRect(QRectF(0, 0, W, H), QColor('#0E1014'))
    p.save()
    p.translate(960, H * 0.34)
    p.scale(1, 900 / 1400)
    bg = QRadialGradient(QPointF(0, 0), 1400)
    bg.setColorAt(0, QColor('#1E2129'))
    bg.setColorAt(0.68, QColor('#0E1014'))
    bg.setColorAt(1, QColor('#0E1014'))
    p.fillRect(QRectF(-960, -H * 0.34 * 1400 / 900, W, H * 1400 / 900), bg)
    p.restore()

    if reveal is not None:
        _paint_real_window(p, t, reveal)
    else:
        _paint_app_window(p, t, version, log_lines if log_lines is not None else _console_lines())

    ch = chrome_state(t)
    hud = ch['hud']

    # grid
    if hud > 0.001:
        p.setPen(QPen(QColor(255, 255, 255, int(255 * 0.05 * 0.5 * hud)), 1))
        for i in range(15):
            p.drawLine(QPointF(i * 128, 0), QPointF(i * 128, H))
        for i in range(9):
            p.drawLine(QPointF(0, i * 128), QPointF(W, i * 128))
        p.setPen(Qt.PenStyle.NoPen)

    # sweep
    if ch['sweep_on'] > 0.001:
        p.save()
        p.setOpacity(ch['sweep_on'])
        y = ch['sweep_y']
        g = _vgrad(y - 90, y + 90, _qc('accent', 0), _qc('accent', 0))
        g.setColorAt(0.5, _qc('accent', 0.14))
        p.fillRect(QRectF(0, y - 90, W, 180), g)
        p.setPen(QPen(_qc('accent', 0.5), 2))
        p.drawLine(QPointF(0, y), QPointF(W, y))
        p.restore()

    # candidate noise points
    if ch['noise_on'] > 0.001:
        p.setBrush(accent)
        for i, n in enumerate(NOISE):
            flick = 0.18 + 0.5 * abs(math.sin(t * 5.5 + n['ph'] * 8))
            p.setOpacity(clamp(flick * ch['noise_on'] * 0.7, 0, 1))
            p.drawEllipse(QPointF(n['x'] * W + math.sin(i) * 60, n['y'] * H), n['r'], n['r'])
        p.setOpacity(1)

    # viewfinder corners and captions
    if hud > 0.001:
        p.save()
        p.setOpacity(hud)
        pen = QPen(_qc('accent', 0.55), 3)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pts in (((96, 150), (96, 96), (150, 96)), ((1824, 150), (1824, 96), (1770, 96)),
                    ((96, 930), (96, 984), (150, 984)), ((1824, 930), (1824, 984), (1770, 984))):
            path = QPainterPath(QPointF(*pts[0]))
            path.lineTo(QPointF(*pts[1]))
            path.lineTo(QPointF(*pts[2]))
            p.drawPath(path)
        mono20 = _font('mono', 20)
        fm = QFontMetricsF(mono20)
        p.setFont(mono20)
        p.setPen(_qc('dim'))
        p.drawText(QPointF(96, 1046), 'CAMERA 0 · 640×480 · MEDIAPIPE HOLISTIC')
        p.setPen(accent)
        txt = '127.0.0.1:1234'
        p.drawText(QPointF(1824 - fm.horizontalAdvance(txt), 1046), txt)
        p.restore()

    # OSC address stream
    if ch['stream_on'] > 0.001:
        p.save()
        mono21 = _font('mono', 21, 500, 0.42)
        for i, a in enumerate(ADDRS):
            pulse = 0.3 + 0.7 * abs(math.sin(t * 6 + i * 1.1))
            p.setOpacity(ch['stream_on'] * clamp(0.35 + pulse * 0.6, 0, 1))
            p.setFont(mono21)
            p.setPen(accent)
            p.drawText(QPointF(1560, 392 + i * 46), a)
            p.setOpacity(ch['stream_on'] * clamp(0.25 + pulse * 0.5, 0, 1))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(1500, 378 + i * 46, 6 + pulse * 34, 4), 2, 2)
        p.restore()

    _paint_icon_and_figure(p, t, reveal.fly_to if reveal is not None else None)
    _paint_wordmark(p, t)


def _paint_icon_and_figure(p: QPainter, t: float, fly_to: tuple | None = None) -> None:
    """Squircle + figure + inscribing circle, flying to the rail."""
    ic = icon_state(t, fly_to)
    fig = figure_state(t)
    pose = pose_at(t)
    cy = fig['cy']

    def px(x):
        return 960 + (x - 0.5) * fig['fw']

    def py(y):
        return cy + (y - fig['anchor_y']) * 700 + fig['bob']

    p.save()
    p.translate(ic['fly_x'], ic['fly_y'])
    p.scale(ic['fly_s'], ic['fly_s'])
    p.translate(-960, -452)

    if ic['sq_a'] > 0.001:
        p.save()
        p.setOpacity(clamp(ic['sq_a'], 0, 1))
        p.translate(960, 452)
        p.scale(ic['sq_s'], ic['sq_s'])
        p.translate(-960, -452)
        _rrect(p, 790, 282, 340, 340, 76, _vgrad(282, 622, QColor('#1E2129'), QColor('#0E1014')),
               _qc('accent', 0.24), 2)
        p.restore()

    p.save()
    p.translate(960, cy)
    p.scale(fig['fig_scale'], fig['fig_scale'])
    p.translate(-960, -cy)

    geo = circle_progress(t)
    if geo > 0.001:
        gcx, gcy, gr = px(0.514), py(0.5628), 0.365 * fig['fw']
        pen = QPen(_qc('accent_2', clamp(geo, 0, 1) * 0.7), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(gcx - gr, gcy - gr, 2 * gr, 2 * gr), 90 * 16, int(-geo * 360 * 16))

    pen = QPen(_qc('accent_2'), 11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for a, b in BONES:
        prog = bone_progress(t, a, b)
        if prog <= 0.001:
            continue
        pa = QPointF(px(pose[a][0]), py(pose[a][1]))
        pb = QPointF(px(pose[b][0]), py(pose[b][1]))
        p.drawLine(pa, QPointF(pa.x() + (pb.x() - pa.x()) * prog, pa.y() + (pb.y() - pa.y()) * prog))

    p.setPen(Qt.PenStyle.NoPen)
    for j in JOINTS:
        ns = node_state(t, j)
        if ns['opacity'] <= 0.001:
            continue
        c = QPointF(px(pose[j][0]) + ns['dx'], py(pose[j][1]) + ns['dy'])
        if ns['flash']:
            p.setBrush(_qc('accent', 0.16 * ns['opacity']))
            _node_circle(p, c, NODE_R[j] * 2.6)
        p.setBrush(_qc('accent', ns['opacity']))
        _node_circle(p, c, ns['radius'])
    p.restore()
    p.restore()


def _paint_wordmark(p: QPainter, t: float) -> None:
    ic = icon_state(t)
    a = ic['word_a']
    if a <= 0.001:
        return
    p.save()
    p.setOpacity(clamp(a, 0, 1))
    p.translate(0, ic['word_y'])
    em = lerp(0.22, 0.06, clamp(a, 0, 1))
    _text_baseline_center(p, 'Gesture', 960, 720, _font('ui', 62, 500, round(em * 62, 2)), _qc('text'))
    _text_baseline_center(p, 'POSE · HANDS · OSC', 960, 772, _font('ui', 24, 400, round(0.16 * 24, 2)), _qc('dim'))
    p.restore()


def _text_baseline_center(p: QPainter, s: str, x: float, y: float, font: QFont, col: QColor) -> None:
    """SVG text-anchor middle at baseline y (trailing letter spacing counted, as SVG does)."""
    p.setFont(font)
    p.setPen(col)
    w = QFontMetricsF(font).horizontalAdvance(s)
    p.drawText(QPointF(x - w / 2, y), s)


def render_frame(t: float, width: int = CANVAS_W, height: int = CANVAS_H) -> QImage:
    """
    Render one frame offscreen.

    Args:
        t: Authored seconds.
        width: Output width in px (height should keep 16:9).
        height: Output height in px.

    Returns:
        The frame as a QImage.
    """
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(PALETTE['bg_deep']))
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                     | QPainter.RenderHint.SmoothPixmapTransform)
    s = min(width / CANVAS_W, height / CANVAS_H)
    p.translate((width - CANVAS_W * s) / 2, (height - CANVAS_H * s) / 2)
    p.scale(s, s)
    paint_frame(p, min(t, TOTAL), _app_version_cached())
    p.end()
    return img


# ============================================================================
# THE SPLASH WIDGET
# ============================================================================
class LaunchAnimation(QWidget):
    """
    Frameless splash that plays the launch animation, then hands over.

    The animation is keyed to wall-clock time (a dropped frame skips ahead, it
    never slows the piece). Any click or key skips straight to the end.
    """

    FRAME_MS = 16

    def __init__(self, window: QWidget | None = None):
        """
        Args:
            window: The main window that will appear when the splash ends.
                The splash is sized and positioned so the mock window lines up
                with it. None: a 1280x720 splash centred on the screen.
        """
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self._window = window
        self._plan: RevealPlan | None = None
        self._fade = None
        self._on_finished: Callable[[], None] | None = None
        self._playing = False
        self._done = False
        self._t = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._version = _app_version_cached()
        self._log = _console_lines()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._place_window()
        self._place()

    # ---- geometry
    def _place_window(self) -> None:
        """Give the (still hidden) window its final position, so the Reveal can land on it exactly."""
        win = self._window
        if win is None or win.testAttribute(Qt.WidgetAttribute.WA_Moved):
            return
        screen = win.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        size = win.size()
        if not win.testAttribute(Qt.WidgetAttribute.WA_Resized):
            hint = win.sizeHint()
            if hint.isValid():
                size = hint
        c = screen.availableGeometry().center()
        win.move(c.x() - size.width() // 2, c.y() - size.height() // 2)

    def _canvas_xform(self) -> tuple[float, float, float]:
        """(scale, offset x, offset y) of the paint transform: canvas units -> splash pixels."""
        s = min(self.width() / CANVAS_W, self.height() / CANVAS_H)
        return s, (self.width() - CANVAS_W * s) / 2, (self.height() - CANVAS_H * s) / 2

    def _build_plan(self) -> RevealPlan | None:
        """Render the real window and map its panes onto the canvas (None: no real window)."""
        win = self._window
        if win is None or not hasattr(win, 'toolbar') or not hasattr(win, 'rail'):
            return None
        try:
            s, ox, oy = self._canvas_xform()
            win.ensurePolished()
            mark = getattr(win.rail, 'mark', None)
            if mark is not None:  # the icon flies into this slot, so the capture leaves it empty
                from PySide6.QtWidgets import QGraphicsOpacityEffect
                fx = QGraphicsOpacityEffect(mark)
                fx.setOpacity(0.0)
                mark.setGraphicsEffect(fx)
            host = win.overlay_host() if hasattr(win, 'overlay_host') else None
            host_was_hidden = host.isHidden() if host is not None else True
            if host is not None:  # first-run/notice cards appear after the hand-over, not inside the capture
                host.hide()
            try:
                pix = win.grab()
            finally:
                if mark is not None:
                    mark.setGraphicsEffect(None)
                if host is not None and not host_was_hidden:
                    host.show()
            origin = win.mapToGlobal(QPoint(0, 0)) if win.isVisible() else win.pos()

            def canvas(widget) -> QRectF:
                tl = widget.mapTo(win, QPoint(0, 0))
                return QRectF((origin.x() - self.x() - ox) / s + tl.x() / s,
                              (origin.y() - self.y() - oy) / s + tl.y() / s,
                              widget.width() / s, widget.height() / s)

            rect = QRectF((origin.x() - self.x() - ox) / s, (origin.y() - self.y() - oy) / s,
                          win.width() / s, win.height() / s)
            live = win.pane('live') if hasattr(win, 'pane') else None
            pieces = [('rail', canvas(win.toolbar)), ('rail', canvas(win.rail))]
            for key, widget in (('stage', getattr(live, 'stage', None)),
                                ('meters', getattr(live, '_meter_row', None)),
                                ('osc', getattr(live, 'column', None)),
                                ('cons', getattr(win, 'console', None))):
                if widget is not None and not widget.isHidden() and widget.width() > 0:
                    pieces.append((key, canvas(widget)))
            fly_to = (960.0, 452.0, 0.105)
            if mark is not None:
                r = canvas(mark)
                fly_to = (r.center().x(), r.center().y(), r.width() / ICON_SIZE)
            return RevealPlan(pix, rect, pieces, fly_to)
        except Exception:
            return None

    def _place(self) -> None:
        """Size the splash so the mock window matches the real one, centred where it will appear."""
        win = self._window
        screen = (win.screen() if win is not None else None) or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        center = None
        if win is not None:
            size = win.size()
            if not win.testAttribute(Qt.WidgetAttribute.WA_Resized):
                hint = win.sizeHint()  # not sized yet: what show() will use
                if hint.isValid():
                    size = hint
            scale = min(size.width() / WIN_W, size.height() / WIN_H)
            if win.testAttribute(Qt.WidgetAttribute.WA_Moved):
                center = QPoint(win.x() + size.width() // 2, win.y() + size.height() // 2)
        else:
            scale = 1280 / CANVAS_W
        if avail is not None:
            scale = min(scale, avail.width() / CANVAS_W, avail.height() / CANVAS_H)
            if center is None:
                center = avail.center()
        w, h = round(CANVAS_W * scale), round(CANVAS_H * scale)
        center = center or QPoint(w // 2, h // 2)
        x, y = center.x() - w // 2, center.y() - h // 2
        if avail is not None:
            x = int(clamp(x, avail.left(), avail.right() - w + 1))
            y = int(clamp(y, avail.top(), avail.bottom() - h + 1))
        self.setGeometry(x, y, w, h)

    # ---- rules
    @staticmethod
    def should_play(config, ready_ms: float | None = None) -> bool:
        """
        Whether to play the animation at all.

        Args:
            config: Config (or anything with `.get(section, key, default)`).
            ready_ms: Unused; kept for callers (the launch time no longer gates it).

        Returns:
            False when `ui.launch_animation` is false or macOS Reduce Motion
            is on; otherwise it plays on every launch.
        """
        try:
            if not config.get('ui', 'launch_animation', True):
                return False
        except Exception:
            pass
        return not reduce_motion_enabled()

    # ---- playback
    @property
    def is_playing(self) -> bool:
        """True between play() and the end/skip."""
        return self._playing

    def play(self, on_finished: Callable[[], None] | None = None) -> None:
        """
        Show the splash and start the 60fps clock.

        Args:
            on_finished: Called once, after the splash has closed, whether the
                animation ran out or was skipped.
        """
        self._on_finished = on_finished
        self._done = False
        self._playing = True
        self._t = 0.0
        self._plan = self._build_plan()
        app = QApplication.instance()
        if app is not None:
            # A Finder/Dock launch activates the app late; keep the splash in front when it does
            app.applicationStateChanged.connect(self._on_app_state)
        self.show()
        self._front()
        self._clock.start()
        self._timer.start()

    def _front(self) -> None:
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _on_app_state(self, state) -> None:
        if self._playing and state == Qt.ApplicationState.ApplicationActive:
            self._front()

    def skip(self) -> None:
        """End immediately and hand over to the window."""
        self._finish()

    def _tick(self) -> None:
        self._t = self._clock.elapsed() / 1000
        if self._t >= TOTAL:
            self._finish()
            return
        self.update()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._playing = False
        self._timer.stop()
        app = QApplication.instance()
        if app is not None:
            try:
                app.applicationStateChanged.disconnect(self._on_app_state)
            except (RuntimeError, TypeError):
                pass
        completed = self._t >= TOTAL and self._plan is not None
        cb, self._on_finished = self._on_finished, None
        if cb is not None:
            cb()
        if completed:
            self._fade_out()
        else:
            self.close()

    def _fade_out(self) -> None:
        """The real window is up behind the identical last frame: dissolve the splash."""
        from PySide6.QtCore import QVariantAnimation
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(HANDOVER_MS)
        anim.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        anim.finished.connect(self.close)
        self._fade = anim
        anim.start()

    # ---- events
    def _can_skip(self) -> bool:
        return self._clock.isValid() and self._clock.elapsed() >= SKIP_GRACE_MS

    def mousePressEvent(self, event) -> None:
        if self._can_skip():
            self.skip()

    def keyPressEvent(self, event) -> None:
        if self._can_skip():
            self.skip()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        s = min(self.width() / CANVAS_W, self.height() / CANVAS_H)
        p.translate((self.width() - CANVAS_W * s) / 2, (self.height() - CANVAS_H * s) / 2)
        p.scale(s, s)
        paint_frame(p, min(self._t, TOTAL), self._version, self._log, self._plan)
        p.end()


def maybe_play(window: QWidget, config, ready_ms: float | None = None) -> LaunchAnimation | None:
    """
    Show `window`, preceded by the launch animation when it should play.

    Args:
        window: The main window; shown when the animation ends or is skipped.
        config: Config for the `ui.launch_animation` switch.
        ready_ms: Unused (the animation no longer depends on launch time).

    Returns:
        The running animation (kept alive on the window), or None when the
        window was simply shown.
    """
    if not LaunchAnimation.should_play(config, ready_ms):
        window.show()
        return None
    anim = LaunchAnimation(window)

    def reveal():
        # The window was positioned before the splash was sized, so it opens
        # exactly where the Reveal drew it (behind the splash, which then fades)
        window.show()
        window.raise_()
        window.activateWindow()

    window._launch_animation = anim
    anim.play(reveal)
    return anim


# ============================================================================
# STANDALONE
# ============================================================================
FRAME_TIMES = (0.6, 1.8, 2.6, 3.8, 4.4, 5.2, 6.0, 7.0, 8.0)


def _bench(width: int, height: int, n: int = 300) -> tuple[float, float]:
    """Mean and max paint ms per frame across the whole timeline."""
    render_frame(0.0, width, height)  # warm fonts
    samples = []
    for i in range(n):
        t0 = time.perf_counter()
        render_frame(TOTAL * i / (n - 1), width, height)
        samples.append((time.perf_counter() - t0) * 1000)
    return sum(samples) / len(samples), max(samples)


def main(argv: list[str] | None = None) -> int:
    """`python -m src.ui.launch [--frames DIR] [--times T...] [--bench]`"""
    ap = argparse.ArgumentParser(description='Play or render the Gesture launch animation')
    ap.add_argument('--frames', metavar='DIR', help='render PNG frames offscreen into DIR instead of playing')
    ap.add_argument('--times', type=float, nargs='*', default=list(FRAME_TIMES))
    ap.add_argument('--bench', action='store_true', help='measure offscreen paint time per frame')
    args = ap.parse_args(argv)

    if args.frames or args.bench:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication(sys.argv[:1])
    if args.frames:
        os.makedirs(args.frames, exist_ok=True)
        for t in args.times:
            path = os.path.join(args.frames, f'74-T{t:.1f}.png')
            render_frame(t).save(path)
            print(path)
    if args.bench:
        for w, h in ((1280, 720), (1920, 1080), (2960, 1664)):  # last: ~1480x832 logical at 2x
            mean, worst = _bench(w, h)
            print(f'{w}x{h}: mean {mean:.2f} ms, max {worst:.2f} ms')
    if args.frames or args.bench:
        return 0

    anim = LaunchAnimation()
    anim.play(app.quit)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
