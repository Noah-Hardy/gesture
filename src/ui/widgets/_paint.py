"""Shared painting helpers for the widget kit (focus ring, geometry)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from ..tokens import Spacing, color


def body_rect(widget) -> QRectF:
    """The widget's rect minus the margin reserved for the focus ring."""
    m = Spacing.FOCUS_MARGIN
    return QRectF(widget.rect()).adjusted(m, m, -m, -m)


def wants_ring(widget) -> bool:
    """True when the focus ring should show (focused, or forced for the gallery)."""
    return widget.hasFocus() or bool(widget.property('ringPreview'))


def draw_focus_ring(p: QPainter, rect: QRectF, radius: float, ring: str = 'accent') -> None:
    """
    Paint the focus ring around `rect`: a 2px bg_deep gap, then a 2px ring.

    Args:
        p: Active painter.
        rect: The control body the ring surrounds.
        radius: The control's corner radius.
        ring: Palette key for the ring (accent, or error for invalid inputs).
    """
    p.save()
    p.setBrush(Qt.BrushStyle.NoBrush)
    for inset, key in ((1, 'bg_deep'), (3, ring)):
        pen = QPen(color(key))
        pen.setWidthF(2)
        p.setPen(pen)
        p.drawRoundedRect(rect.adjusted(-inset, -inset, inset, inset), radius + inset, radius + inset)
    p.restore()
