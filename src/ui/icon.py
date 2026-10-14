#!/usr/bin/env python3
"""
Icon Painter

Draws the Gesture mark with QPainter from the shared geometry in
src/icon_geometry.py: the rail mark, the application icon, and (later) the
status-bar item. Same tiers and colours as scripts/make_icon.py's raster export.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)

from src.icon_geometry import (
    ACCENT, BG_BOTTOM, BG_TOP, CIRCLE, CIRCLE_ALPHA, CIRCLE_WIDTH, CORNER, DIM,
    TIERS, scaled_point, tier_for,
)

APP_ICON_SIZES = (16, 32, 64, 128, 256, 512)


def paint_mark(p: QPainter, rect: QRectF, tier_name: str | None = None) -> None:
    """
    Paint the mark into `rect`.

    Args:
        p: Active painter.
        rect: Square target rectangle.
        tier_name: Detail tier key; defaults to the tier for the rect's size.
    """
    size = rect.width()
    tier = TIERS[tier_name or tier_for(int(size))]
    scale = tier.scale
    unit = size / 100.0
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.translate(rect.topLeft())

    # Rounded-square body clips everything else
    body = QPainterPath()
    body.addRoundedRect(QRectF(0, 0, size, size), CORNER * size, CORNER * size)
    p.setClipPath(body)
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor(*BG_TOP))
    grad.setColorAt(1, QColor(*BG_BOTTOM))
    p.fillPath(body, grad)

    def pt(point) -> QPointF:
        x, y = scaled_point(point, scale)
        return QPointF(x * unit, y * unit)

    if tier.circle:
        ring = QColor(DIM)
        ring.setAlphaF(CIRCLE_ALPHA)
        p.setPen(QPen(ring, max(1.0, CIRCLE_WIDTH * unit)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        c = pt((CIRCLE[0], CIRCLE[1]))
        r = CIRCLE[2] * scale * unit
        p.drawEllipse(c, r, r)

    pen = QPen(QColor(DIM), tier.weight * scale * unit)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    for chain in tier.bones:
        path = QPainterPath(pt(chain[0]))
        for point in chain[1:]:
            path.lineTo(pt(point))
        p.drawPath(path)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    for point, radius in tier.nodes:
        rr = radius * scale * unit
        p.drawEllipse(pt(point), rr, rr)
    p.restore()


def mark_pixmap(size: int, dpr: float = 2.0, tier_name: str | None = None) -> QPixmap:
    """
    Render the mark to a pixmap.

    Args:
        size: Logical edge length in pixels.
        dpr: Device pixel ratio of the pixmap.
        tier_name: Optional detail tier override.

    Returns:
        A transparent-cornered pixmap of size x dpr pixels.
    """
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    paint_mark(p, QRectF(0, 0, size, size), tier_name)
    p.end()
    return px


def app_icon() -> QIcon:
    """The application icon: one pixmap per size, each at its own detail tier."""
    icon = QIcon()
    for s in APP_ICON_SIZES:
        icon.addPixmap(mark_pixmap(s, 1.0))
    return icon
