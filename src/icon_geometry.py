#!/usr/bin/env python3
"""
Gesture Icon Geometry

Qt-free, PIL-free description of the app icon: one asymmetric pose (left arm
raised, right arm level, right foot stepping out) read as landmark nodes inside
a thin measuring circle, with detail dropped by size tier so the mark stays
legible from 1024px down to 16px.

Shared by scripts/make_icon.py (raster export) and by anything that draws the
mark at runtime (rail mark, status-bar item, launch animation).

All coordinates are in a 0-100 square, y growing downward. A tier's ``scale``
is applied about (50, 50) before drawing; weights and radii are in the same
0-100 units and are scaled with it.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from typing import Dict, List, NamedTuple, Tuple

Point = Tuple[float, float]


# ============================================================================
# STYLE
# ============================================================================
ACCENT = '#3DDC84'         # landmark nodes
DIM = '#2F7856'            # bones and the measuring circle
BG_TOP = (30, 33, 41)      # #1E2129
BG_BOTTOM = (14, 16, 20)   # #0E1014
CORNER = 0.225             # corner radius as a fraction of the side
CIRCLE_ALPHA = 0.58        # opacity of the measuring circle
CIRCLE_WIDTH = 0.95        # circle stroke width, 0-100 units


# ============================================================================
# GEOMETRY
# ============================================================================
P: Dict[str, Point] = {
    'head': (50, 29), 'chest': (50, 41.5), 'hip': (50, 52),
    'ls': (43, 40.5), 'rs': (57, 40.5),
    'le': (34, 36), 'lh': (28, 27.5),
    're': (66, 43.5), 'rh': (74, 44),
    'lk': (44, 63.5), 'lf': (42.5, 75.5),
    'rk': (58.5, 62.5), 'rf': (66.5, 73.5),
}
CIRCLE = (51, 51, 26.5)  # cx, cy, r


class Tier(NamedTuple):
    """One level of icon detail.

    Attributes:
        scale: Extra scale about the centre (bigger at small sizes).
        bones: Polylines, each a list of points.
        nodes: (point, radius) pairs.
        weight: Bone stroke width.
        circle: Whether the measuring circle is drawn.
    """
    scale: float
    bones: List[List[Point]]
    nodes: List[Tuple[Point, float]]
    weight: float
    circle: bool


TIERS: Dict[str, Tier] = {
    'full': Tier(1.00, [
        [(50, 34), P['hip']],
        [P['lh'], P['le'], P['ls'], P['chest'], P['rs'], P['re'], P['rh']],
        [P['hip'], P['lk'], P['lf']],
        [P['hip'], P['rk'], P['rf']],
    ], [
        (P['head'], 5.5), (P['chest'], 2.95), (P['hip'], 2.95),
        (P['le'], 2.35), (P['lh'], 3.5), (P['re'], 2.35), (P['rh'], 3.5),
        (P['lk'], 2.35), (P['lf'], 3.5), (P['rk'], 2.35), (P['rf'], 3.5),
    ], 3.3, True),
    'mid': Tier(1.08, [
        [(50, 34), P['hip']],
        [P['lh'], P['le'], P['chest'], P['re'], P['rh']],
        [P['hip'], P['lk'], P['lf']],
        [P['hip'], P['rk'], P['rf']],
    ], [
        (P['head'], 6.6), (P['chest'], 3.6), (P['hip'], 3.6),
        (P['lh'], 4.4), (P['rh'], 4.4), (P['lf'], 4.4), (P['rf'], 4.4),
    ], 4.6, False),
    'small': Tier(1.12, [
        [(50, 34), (50, 53)],
        [(28.5, 28), (50, 42), (74, 44.5)],
        [(50, 53), (43, 75.5)],
        [(50, 53), (66.5, 73.5)],
    ], [
        ((50, 29), 9.4), ((50, 53), 5.0),
        ((28.5, 28), 6.4), ((74, 44.5), 6.4), ((43, 75.5), 6.4), ((66.5, 73.5), 6.4),
    ], 7.5, False),
    'tiny': Tier(1.18, [
        [(50, 34), (50, 55)],
        [(28, 29), (50, 43), (74, 45)],
        [(50, 55), (42, 78)],
        [(50, 55), (67, 76)],
    ], [
        ((50, 30), 12.0), ((28, 29), 8.4), ((74, 45), 8.4),
        ((42, 78), 8.4), ((67, 76), 8.4),
    ], 11.0, False),
}


def tier_for(size: int) -> str:
    """Pick the detail tier for a rendered size in pixels.

    Args:
        size: Edge length in pixels.

    Returns:
        Key into TIERS: full (>=128), mid (>=64), small (>=32), tiny (<32).
    """
    if size >= 128:
        return 'full'
    if size >= 64:
        return 'mid'
    if size >= 32:
        return 'small'
    return 'tiny'


def scaled_point(p: Point, scale: float) -> Point:
    """Apply a tier's scale about the centre of the 0-100 square.

    Args:
        p: Point in 0-100 coordinates.
        scale: Tier scale.

    Returns:
        The scaled point, still in 0-100 coordinates.
    """
    return ((p[0] - 50) * scale + 50, (p[1] - 50) * scale + 50)
