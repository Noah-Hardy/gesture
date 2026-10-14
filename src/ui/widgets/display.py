"""
Display widgets: Meter, Sparkline, SpecTable, CodeBlock, ColorSwatch,
StatusDot, PathRow, RestartChip.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ..tokens import Font, Radius, Spacing, color, panel_gradient
from .controls import Button

# Design: the meter bar's `transition: width .4s cubic-bezier(.4,0,.2,1)`
METER_BAR_MS = 400


def _bezier() -> QEasingCurve:
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.4, 0.0), QPointF(0.2, 1.0), QPointF(1.0, 1.0))
    return c


def _aa(p: QPainter) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)


# ============================================================================
# METER
# ============================================================================
class Meter(QWidget):
    """
    Stat card: 11px uppercase label, 22px tabular value with unit, 4px bar.

    Args:
        label: Caption (rendered uppercase).
        value: Display text for the value.
        unit: Unit shown beside the value.
        fraction: Bar fill 0..1.
        value_px: Value text size; 22 by default, fixed smaller for compound readouts.
    """

    HEIGHT = 84

    def __init__(self, label: str = '', value: str = '--', unit: str = '',
                 fraction: float = 0.0, parent: QWidget | None = None, value_px: int = 22):
        super().__init__(parent)
        self._label, self._value, self._unit, self._frac = label, value, unit, fraction
        self._value_px = value_px
        self._shown = max(0.0, min(1.0, fraction))  # bar fill on screen; glides to _frac
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(METER_BAR_MS)
        self._anim.setEasingCurve(_bezier())
        self._anim.valueChanged.connect(self._on_glide)
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(150, self.HEIGHT)

    def set_value(self, value: str, fraction: float | None = None, unit: str | None = None) -> None:
        """Update the readout (and optionally bar fraction and unit)."""
        if value == self._value and unit in (None, self._unit) and (
                fraction is None or max(0.0, min(1.0, fraction)) == self._frac):
            return   # nothing changed: no repaint (status arrives 10x/s)
        self._value = value
        if fraction is not None:
            frac = max(0.0, min(1.0, fraction))
            if frac != self._frac:
                self._frac = frac
                # transition: width .4s cubic-bezier(.4,0,.2,1); only while on screen
                if self.isVisible():
                    self._anim.stop()
                    self._anim.setStartValue(self._shown)
                    self._anim.setEndValue(frac)
                    self._anim.start()
                else:
                    self._shown = frac
        if unit is not None:
            self._unit = unit
        self.update()

    def _on_glide(self, v) -> None:
        self._shown = float(v)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        p.setBrush(panel_gradient(r.height()))
        p.setPen(QPen(color('border'), 1))
        p.drawRoundedRect(r, Radius.CARD, Radius.CARD)
        x, w = 12, self.width() - 24
        f = Font.ui(11)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.3)
        p.setFont(f)
        p.setPen(color('faint'))
        p.drawText(QRectF(x, 11, w, 14), Qt.AlignmentFlag.AlignVCenter, self._label.upper())
        vf = Font.ui(self._value_px, 500)
        p.setFont(vf)
        p.setPen(color('text'))
        vw = QFontMetrics(vf).horizontalAdvance(self._value)
        p.drawText(QRectF(x, 32, w, 28), Qt.AlignmentFlag.AlignVCenter, self._value)
        if self._unit:
            p.setFont(Font.ui(11))
            p.setPen(color('dim'))
            p.drawText(QRectF(x + vw + 5, 32, w - vw - 5, 28),
                       Qt.AlignmentFlag.AlignBottom, self._unit)
            # bottom-align unit to the value baseline area
        bar = QRectF(x, self.HEIGHT - 11 - 4, w, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('border'))
        p.drawRoundedRect(bar, 2, 2)
        p.setBrush(color('accent'))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * self._shown, 4), 2, 2)


# ============================================================================
# SPARKLINE
# ============================================================================
class Sparkline(QWidget):
    """
    22 bars x 3px (2px gap), 16px maximum height, fed from a ring buffer and
    redrawn at 2 Hz regardless of how fast values arrive.
    """

    BARS, BAR_W, GAP, HEIGHT, REDRAW_MS = 22, 3, 2, 16, 500

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._buf: deque[float] = deque([0.0] * self.BARS, maxlen=self.BARS)
        self._dirty = False
        self.setFixedSize(self.BARS * self.BAR_W + (self.BARS - 1) * self.GAP, self.HEIGHT)
        self._timer = QTimer(self)
        self._timer.setInterval(self.REDRAW_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def push(self, v: float) -> None:
        """Append a sample (0..1). Cheap: the repaint happens on the 2 Hz timer."""
        self._buf.append(max(0.0, min(1.0, float(v))))
        self._dirty = True

    def set_values(self, values) -> None:
        """Replace the buffer with the last 22 of `values`."""
        self._buf = deque([0.0] * self.BARS, maxlen=self.BARS)
        for v in list(values)[-self.BARS:]:
            self._buf.append(max(0.0, min(1.0, float(v))))
        self._dirty = True
        self._tick()

    def values(self) -> list[float]:
        return list(self._buf)

    def _tick(self) -> None:
        if self._dirty:
            self._dirty = False
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        for i, raw in enumerate(self._buf):
            v = max(0.12, raw)
            h = max(2, round(v * self.HEIGHT))
            x = i * (self.BAR_W + self.GAP)
            p.setBrush(color('accent', 0.25 + v * 0.6))
            p.drawRoundedRect(QRectF(x, self.HEIGHT - h, self.BAR_W, h), 1, 1)


# ============================================================================
# SPEC TABLE
# ============================================================================
class SpecTable(QWidget):
    """
    Header row on `header`, zebra rows on `row_alt`, monospace accent first
    column, right-aligned last column (when there are more than two).

    Args:
        rows: First row is the header; the rest are body rows (lists of str).
        cols: Column widths in px; 0 means "stretch" (shares what is left).
    """

    ROW = 30

    def __init__(self, rows: list[list[str]], cols: tuple[int, ...] = (150, 0, 74),
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._rows, self._cols = rows, cols
        self.setFixedHeight(len(rows) * self.ROW + 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(sum(c or 160 for c in self._cols) + 24 * len(self._cols), self.height())

    def _col_rects(self, top: float) -> list[QRectF]:
        inner = self.width() - 2 - 22
        gaps = 12 * (len(self._cols) - 1)
        fixed = sum(self._cols)
        stretch = [i for i, c in enumerate(self._cols) if c == 0]
        share = max(0, inner - gaps - fixed) / len(stretch) if stretch else 0
        x, out = 1 + 11, []
        for c in self._cols:
            w = c or share
            out.append(QRectF(x, top, w, self.ROW))
            x += w + 12
        return out

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        outer = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        clip = QPainterPath()
        clip.addRoundedRect(outer, Radius.TROUGH, Radius.TROUGH)
        p.setClipPath(clip)
        for ri, cells in enumerate(self._rows):
            top = 1 + ri * self.ROW
            band = QRectF(1, top, self.width() - 2, self.ROW)
            if ri == 0:
                p.fillRect(band, color('header'))
                p.fillRect(QRectF(1, top + self.ROW - 1, band.width(), 1), color('border'))
            elif ri % 2 == 0:
                p.fillRect(band, color('row_alt'))
            for ci, r in enumerate(self._col_rects(top)):
                text = cells[ci] if ci < len(cells) else ''
                last = ci == len(self._cols) - 1 and len(self._cols) > 2
                align = (Qt.AlignmentFlag.AlignRight if last else Qt.AlignmentFlag.AlignLeft) \
                    | Qt.AlignmentFlag.AlignVCenter
                if ri == 0:
                    f = Font.ui(11.5)
                    f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.1)
                    p.setFont(f)
                    p.setPen(color('faint'))
                    text = text.upper()
                elif ci == 0:
                    p.setFont(Font.mono(11.5))
                    p.setPen(color('accent'))
                else:
                    p.setFont(Font.ui(11.5))
                    p.setPen(color('text_2'))
                el = QFontMetrics(p.font()).elidedText(text, Qt.TextElideMode.ElideRight, int(r.width()))
                p.drawText(r, align, el)
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color('border'), 1))
        p.drawRoundedRect(outer, Radius.TROUGH, Radius.TROUGH)


# ============================================================================
# CODE BLOCK
# ============================================================================
class CodeBlock(QFrame):
    """Monospace 11px block on `console`, 9px radius, selectable text, wraps anywhere."""

    def __init__(self, text: str = '', parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('codeBlock')
        self.setStyleSheet(
            f"QFrame#codeBlock {{ background: {color('console').name()};"
            f" border: 1px solid {color('border').name()}; border-radius: {Radius.TROUGH}px; }}"
            " QLabel { background: transparent; border: none; }")
        self._label = QLabel(text, self)
        self._label.setFont(Font.mono(11))
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._label.setStyleSheet(f"color: {color('text_2').name()};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self._label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def setText(self, text: str) -> None:
        self._label.setText(text)

    def text(self) -> str:
        return self._label.text()


# ============================================================================
# COLOR SWATCH / STATUS DOT / RESTART CHIP
# ============================================================================
class ColorSwatch(QWidget):
    """30x30 rounded swatch (8px radius, border_2 outline) with a mono hex readout."""

    def __init__(self, hex_color: str = '#3DDC84', parent: QWidget | None = None):
        super().__init__(parent)
        self._hex = hex_color
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._chip = QWidget(self)
        self._chip.setFixedSize(30, 30)
        self._chip.paintEvent = self._paint_chip  # type: ignore[method-assign]
        self._text = QLabel(hex_color, self)
        self._text.setFont(Font.mono(12))
        self._text.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
        lay.addWidget(self._chip)
        lay.addWidget(self._text)
        lay.addStretch(1)

    def setColor(self, hex_color: str) -> None:
        self._hex = hex_color
        self._text.setText(hex_color)
        self._chip.update()

    def colorHex(self) -> str:
        return self._hex

    def _paint_chip(self, _e) -> None:
        p = QPainter(self._chip)
        _aa(p)
        p.setBrush(color(self._hex))
        p.setPen(QPen(color('border_2'), 1))
        p.drawRoundedRect(QRectF(.5, .5, 29, 29), Radius.CONTROL, Radius.CONTROL)


class StatusDot(QWidget):
    """6px dot plus 13px text (e.g. camera permission: Granted / Denied by macOS)."""

    def __init__(self, text: str = '', tone: str = 'accent', parent: QWidget | None = None):
        super().__init__(parent)
        self._tone = tone
        self._label = QLabel(text, self)
        self._label.setFont(Font.ui(13))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._dot = QWidget(self)
        self._dot.setFixedSize(6, 6)
        self._dot.paintEvent = self._paint_dot  # type: ignore[method-assign]
        lay.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._label)
        lay.addStretch(1)
        self.setFixedHeight(Spacing.ROW_HEIGHT)
        self._apply()

    def set_state(self, text: str, tone: str) -> None:
        """Tone is a palette key: accent (ok), warn, error, dim."""
        self._label.setText(text)
        self._tone = tone
        self._apply()

    def tone(self) -> str:
        return self._tone

    def _apply(self) -> None:
        self._label.setStyleSheet(f"color: {color(self._tone).name()}; background: transparent;")
        self._dot.update()

    def _paint_dot(self, _e) -> None:
        p = QPainter(self._dot)
        _aa(p)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(self._tone))
        p.drawEllipse(QPointF(3, 3), 3, 3)


class RestartChip(QWidget):
    """Small uppercase 'RESTART' chip: warn text, 1px warn outline at 33% alpha."""

    def __init__(self, text: str = 'restart', parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text.upper()
        self.setAccessibleName('Requires restart')
        f = self._font()
        self.setFixedSize(QFontMetrics(f).horizontalAdvance(self._text) + 13 * 1 + 12,
                          QFontMetrics(f).height() + 5)

    def _font(self):
        f = Font.ui(10)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 0.8)
        return f

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color('warn', 0.33), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), Radius.CHIP, Radius.CHIP)
        p.setFont(self._font())
        p.setPen(color('warn'))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


# ============================================================================
# PATH ROW
# ============================================================================
class _ElidedLabel(QLabel):
    """QLabel that middle-elides its text to the current width."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = ''

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        fm = QFontMetrics(self.font())
        self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(20, self.width())))


class PathRow(QWidget):
    """
    Elided monospace path (11px, dim) followed by a button (default "Reveal").

    Signals:
        activated(): the button was pressed.
    """

    activated = Signal()

    def __init__(self, path: str = '', button: str = 'Reveal', parent: QWidget | None = None):
        super().__init__(parent)
        self._path = path
        self._label = _ElidedLabel(self)
        self._label.setFont(Font.mono(11))
        self._label.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._button = Button(button, parent=self)
        self._button.clicked.connect(self.activated)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10 - Spacing.FOCUS_MARGIN)
        lay.addWidget(self._label, 1)
        lay.addWidget(self._button)
        self.setFocusProxy(self._button)
        self.setMinimumHeight(Spacing.ROW_HEIGHT + 2 * Spacing.FOCUS_MARGIN)
        self.setPath(path)

    def setPath(self, path: str) -> None:
        self._path = path
        self._label.setToolTip(path)
        self._label.set_full_text(path)

    def path(self) -> str:
        return self._path
