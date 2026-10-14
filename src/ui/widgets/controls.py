"""
Interactive controls: Toggle, RangeSlider, SegmentedChoice, Select, TextField, Button.

Each control paints itself (Qt stylesheets cannot draw the two-layer focus
ring) and reserves `Spacing.FOCUS_MARGIN` px around its body for it.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QWidget,
)

from ..tokens import Font, Radius, Spacing, color
from ._paint import body_rect, draw_focus_ring, wants_ring

_M = Spacing.FOCUS_MARGIN
_ENTER_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
_ACTIVATE_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)


def _aa(p: QPainter) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)


# ============================================================================
# TOGGLE
# ============================================================================
class Toggle(QAbstractButton):
    """
    40x22 pill switch with a 16px knob and a 180ms transition.

    Space or Enter toggles it. Qt has no "switch" accessible role, so the
    accessible description is "switch" and the checked state is exposed
    through the normal check-box accessibility interface.

    Signals:
        toggled(bool): inherited from QAbstractButton.
    """

    WIDTH, HEIGHT, KNOB = 40, 22, 16

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleDescription('switch')
        self.setFixedSize(self.WIDTH + 2 * _M, self.HEIGHT + 2 * _M)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH + 2 * _M, self.HEIGHT + 2 * _M)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _on_anim(self, v) -> None:
        self._pos = float(v)
        self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() in _ACTIVATE_KEYS:
            if not e.isAutoRepeat():
                self.toggle()
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:
        if e.key() in _ACTIVATE_KEYS:
            e.accept()
            return
        super().keyReleaseEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = body_rect(self)
        t = self._pos
        off_bg, on_bg = color('border'), color('accent')
        off_bd, on_bd = color('border_2'), color('accent')
        mix = lambda a, b: QColor.fromRgbF(  # noqa: E731
            a.redF() + (b.redF() - a.redF()) * t,
            a.greenF() + (b.greenF() - a.greenF()) * t,
            a.blueF() + (b.blueF() - a.blueF()) * t)
        p.setBrush(mix(off_bg, on_bg))
        p.setPen(QPen(mix(off_bd, on_bd), 1))
        h = r.height() / 2
        p.drawRoundedRect(r.adjusted(.5, .5, -.5, -.5), h, h)
        travel = r.width() - 2 * 3 - self.KNOB
        kx = r.left() + 3 + travel * t
        ky = r.top() + (r.height() - self.KNOB) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(color('dim'), color('on_accent')))
        p.drawEllipse(QRectF(kx, ky, self.KNOB, self.KNOB))
        if wants_ring(self):
            draw_focus_ring(p, r, h)


# ============================================================================
# RANGE SLIDER
# ============================================================================
class _Track(QWidget):
    """The focusable slider track of a RangeSlider."""

    HALO, HALO_DRAG, THUMB = 3, 5, 13
    INSET = 10

    valueChanged = Signal(float)

    def __init__(self, value: float, parent: QWidget | None = None):
        super().__init__(parent)
        self._value = value
        self._dragging = False
        self._dirty = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(20 + 2 * _M)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @staticmethod
    def snap(v: float) -> float:
        return round(min(1.0, max(0.0, v)) * 20) / 20

    def set_value(self, v: float) -> None:
        v = self.snap(v)
        if v != self._value:
            self._value = v
            self.update()
            self.valueChanged.emit(v)

    def _from_x(self, x: float) -> float:
        b = body_rect(self)
        left, width = b.left() + self.INSET, b.width() - 2 * self.INSET
        return (x - left) / width if width > 0 else 0.0

    def keyPressEvent(self, e) -> None:
        step = {Qt.Key.Key_Left: -0.05, Qt.Key.Key_Down: -0.05,
                Qt.Key.Key_Right: 0.05, Qt.Key.Key_Up: 0.05}.get(e.key())
        if step is not None:
            self.set_value(self._value + step)
        elif e.key() == Qt.Key.Key_Home:
            self.set_value(0.0)
        elif e.key() == Qt.Key.Key_End:
            self.set_value(1.0)
        else:
            super().keyPressEvent(e)
            return
        e.accept()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._dragging = True
            self.set_value(self._from_x(e.position().x()))
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._dragging:
            self.set_value(self._from_x(e.position().x()))

    def mouseReleaseEvent(self, _e) -> None:
        self._dragging = False
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        b = body_rect(self)
        left, width = b.left() + self.INSET, b.width() - 2 * self.INSET
        cy = b.center().y()
        track = QRectF(left, cy - 1.5, width, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('border'))
        p.drawRoundedRect(track, 1.5, 1.5)
        p.setBrush(color('accent'))
        p.drawRoundedRect(QRectF(left, cy - 1.5, width * self._value, 3), 1.5, 1.5)
        cx = left + width * self._value
        halo = self.HALO_DRAG if self._dragging else self.HALO
        rad = self.THUMB / 2 + halo
        p.setBrush(color('accent', 0.20 if self._dragging else 0.133))
        p.drawEllipse(QPointF(cx, cy), rad, rad)
        p.setBrush(color('accent'))
        p.drawEllipse(QPointF(cx, cy), self.THUMB / 2, self.THUMB / 2)
        if wants_ring(self) or self._dragging:
            draw_focus_ring(p, b, 4)


class RangeSlider(QWidget):
    """
    0-1 slider in 0.05 steps with a 30px tabular value readout.

    Left/Right (and Up/Down) step by 0.05; Home/End jump to the ends.

    Signals:
        valueChanged(float): emitted with the snapped value.
    """

    valueChanged = Signal(float)

    def __init__(self, value: float = 0.5, parent: QWidget | None = None):
        super().__init__(parent)
        self._dirty = False
        self._track = _Track(_Track.snap(value), self)
        self._readout = QLabel(self)
        self._readout.setFixedWidth(30)
        self._readout.setFont(Font.ui(13))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14 - _M)
        lay.addWidget(self._track, 1)
        lay.addWidget(self._readout)
        self.setFocusProxy(self._track)
        self.setMaximumWidth(Spacing.SLIDER_MAX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._track.valueChanged.connect(self._on_value)
        self._refresh()

    def sizeHint(self) -> QSize:
        return QSize(Spacing.SLIDER_MAX, self._track.height())

    def value(self) -> float:
        return self._track._value

    def setValue(self, v: float) -> None:
        self._track.set_value(v)

    def set_dirty(self, dirty: bool) -> None:
        """Colour the readout accent while the value differs from the applied one."""
        self._dirty = dirty
        self._refresh()

    def _on_value(self, v: float) -> None:
        self._refresh()
        self.valueChanged.emit(v)

    def _refresh(self) -> None:
        self._readout.setText(f'{self._track._value:.2f}')
        self._readout.setStyleSheet(
            f"color: {'#3DDC84' if self._dirty else '#E6E8EC'}; background: transparent;")
        self._track.update()


# ============================================================================
# SEGMENTED CHOICE
# ============================================================================
class SegmentedChoice(QWidget):
    """
    Radio group in a trough (9px radius). The selected option is an accent
    fill with dark text. One Tab stop; Left/Right move the selection.

    Signals:
        currentChanged(str): the newly selected option.
    """

    currentChanged = Signal(str)
    PAD_X, PAD_Y, GAP, PAD = 14, 5, 4, 3

    def __init__(self, options: list[str], current: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._options = list(options)
        self._index = self._options.index(current) if current in self._options else 0
        self._dirty = False
        self._hover = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleDescription('radio group')
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.updateGeometry()

    def _font(self, selected: bool = False):
        return Font.ui(12.5, 500 if selected else 400)

    def _option_rects(self) -> list[QRectF]:
        fm = QFontMetrics(self._font(True))
        x = _M + 1 + self.PAD
        h = fm.height() + 2 * self.PAD_Y
        top = _M + 1 + self.PAD
        out = []
        for o in self._options:
            w = fm.horizontalAdvance(o) + 2 * self.PAD_X
            out.append(QRectF(x, top, w, h))
            x += w + self.GAP
        return out

    def sizeHint(self) -> QSize:
        rects = self._option_rects()
        right = rects[-1].right() if rects else 0
        h = rects[0].height() if rects else 20
        return QSize(int(right + self.PAD + 1 + _M), int(h + 2 * (self.PAD + 1) + 2 * _M))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def current(self) -> str:
        return self._options[self._index] if self._options else ''

    def setCurrent(self, value: str) -> None:
        if value in self._options and self._options.index(value) != self._index:
            self._index = self._options.index(value)
            self.update()
            self.currentChanged.emit(value)

    def set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.update()

    def _select(self, i: int) -> None:
        if 0 <= i < len(self._options):
            self.setCurrent(self._options[i])

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._select(self._index - 1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._select(self._index + 1)
        else:
            super().keyPressEvent(e)
            return
        e.accept()

    def _hit(self, pos) -> int:
        for i, r in enumerate(self._option_rects()):
            if r.adjusted(-self.GAP / 2, 0, self.GAP / 2, 0).contains(pos):
                return i
        return -1

    def mousePressEvent(self, e) -> None:
        i = self._hit(e.position())
        if i >= 0:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._select(i)

    def mouseMoveEvent(self, e) -> None:
        i = self._hit(e.position())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = -1
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        trough = body_rect(self)
        p.setBrush(color('field'))
        p.setPen(QPen(color('accent' if self._dirty else 'border_2'), 1))
        p.drawRoundedRect(trough.adjusted(.5, .5, -.5, -.5), Radius.TROUGH, Radius.TROUGH)
        for i, r in enumerate(self._option_rects()):
            sel = i == self._index
            if sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color('accent'))
                p.drawRoundedRect(r, Radius.OPTION, Radius.OPTION)
            p.setFont(self._font(sel))
            p.setPen(color('on_accent' if sel else ('text_2' if i == self._hover else 'dim')))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._options[i])
            if wants_ring(self) and sel:
                draw_focus_ring(p, r, Radius.OPTION)


# ============================================================================
# SELECT
# ============================================================================
class _SelectPopup(QFrame):
    """The custom 268px popup list with a check-mark column."""

    ROW = 30

    def __init__(self, owner: Select):
        super().__init__(owner.window(), Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._owner = owner
        self._hover = owner._index
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        n = len(owner._options)
        self.setFixedSize(Spacing.CONTROL_WIDTH, 2 * 5 + n * self.ROW)

    def _row_rect(self, i: int) -> QRectF:
        return QRectF(5, 5 + i * self.ROW, self.width() - 10, self.ROW)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        p.setBrush(color('panel'))
        p.setPen(QPen(color('border_2'), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), Radius.TROUGH, Radius.TROUGH)
        p.setFont(Font.ui(12.5))
        for i, o in enumerate(self._owner._options):
            r = self._row_rect(i)
            sel = i == self._owner._index
            if sel or i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color('accent', 0.07) if sel else color('row_alt'))
                p.drawRoundedRect(r, Radius.OPTION, Radius.OPTION)
            p.setPen(color('accent' if sel else 'text_2'))
            if sel:
                p.drawText(QRectF(r.left() + 9, r.top(), 10, r.height()),
                           Qt.AlignmentFlag.AlignVCenter, '✓')
            p.drawText(QRectF(r.left() + 27, r.top(), r.width() - 36, r.height()),
                       Qt.AlignmentFlag.AlignVCenter, o)

    def _index_at(self, y: float) -> int:
        i = int((y - 5) // self.ROW)
        return i if 0 <= i < len(self._owner._options) else -1

    def mouseMoveEvent(self, e) -> None:
        i = self._index_at(e.position().y())
        if i != self._hover:
            self._hover = i
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        i = self._index_at(e.position().y())
        if i >= 0:
            self._owner._choose(i)
        self.close()

    def keyPressEvent(self, e) -> None:
        n = len(self._owner._options)
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close()
        elif k == Qt.Key.Key_Down:
            self._hover = min(n - 1, self._hover + 1)
            self.update()
        elif k == Qt.Key.Key_Up:
            self._hover = max(0, self._hover - 1)
            self.update()
        elif k in _ACTIVATE_KEYS:
            if self._hover >= 0:
                self._owner._choose(self._hover)
            self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e) -> None:
        self._owner._popup = None
        self._owner.update()
        super().closeEvent(e)


class Select(QWidget):
    """
    268x30 combobox with a custom popup that has a check-mark column.

    Enter/Space opens the popup; Up/Down/Enter/Esc work inside it. The border
    turns accent while open or dirty.

    Signals:
        currentChanged(str): the newly chosen option.
    """

    currentChanged = Signal(str)

    def __init__(self, options: list[str], current: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._options = list(options)
        self._index = self._options.index(current) if current in self._options else 0
        self._dirty = False
        self._dim = False
        self._popup: _SelectPopup | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleDescription('combobox')
        self.setFixedSize(Spacing.CONTROL_WIDTH + 2 * _M, Spacing.ROW_HEIGHT + 2 * _M)

    def sizeHint(self) -> QSize:
        return self.size()

    def currentText(self) -> str:
        return self._options[self._index] if self._options else ''

    def setCurrentText(self, text: str) -> None:
        if text in self._options and self._options.index(text) != self._index:
            self._index = self._options.index(text)
            self.update()
            self.currentChanged.emit(text)

    def setOptions(self, options: list[str], current: str | None = None) -> None:
        self._options = list(options)
        self._index = self._options.index(current) if current in self._options else 0
        self.update()

    def set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.update()

    def set_dim(self, dim: bool) -> None:
        """Show the value in the faint colour (placeholder-like, e.g. 'Auto')."""
        self._dim = dim
        self.update()

    def is_open(self) -> bool:
        return self._popup is not None

    def open_popup(self) -> None:
        if self._popup is not None or not self._options:
            return
        self._popup = _SelectPopup(self)
        origin = self.mapToGlobal(QPointF(_M, _M + Spacing.ROW_HEIGHT + 4).toPoint())
        self._popup.move(origin)
        self._popup.show()
        self._popup.setFocus()
        self.update()

    def _choose(self, i: int) -> None:
        self.setCurrentText(self._options[i])

    def keyPressEvent(self, e) -> None:
        if e.key() in _ACTIVATE_KEYS:
            self.open_popup()
            e.accept()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.open_popup()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = body_rect(self)
        open_ = self._popup is not None
        p.setBrush(color('field'))
        p.setPen(QPen(color('accent' if (open_ or self._dirty) else 'border_2'), 1))
        p.drawRoundedRect(r.adjusted(.5, .5, -.5, -.5), Radius.CONTROL, Radius.CONTROL)
        p.setFont(Font.ui(13))
        fm = QFontMetrics(p.font())
        p.setPen(color('faint' if (self._dim and not self._dirty) else 'text'))
        text = fm.elidedText(self.currentText(), Qt.TextElideMode.ElideRight, int(r.width() - 46))
        p.drawText(QRectF(r.left() + 10, r.top(), r.width() - 46, r.height()),
                   Qt.AlignmentFlag.AlignVCenter, text)
        # chevron (rotates when open)
        c = QPointF(r.right() - 16, r.center().y())
        path = QPainterPath()
        s = -1 if open_ else 1
        path.moveTo(c.x() - 4, c.y() - 2 * s)
        path.lineTo(c.x() + 4, c.y() - 2 * s)
        path.lineTo(c.x(), c.y() + 2.5 * s)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('dim'))
        p.drawPath(path)
        if wants_ring(self):
            draw_focus_ring(p, r, Radius.CONTROL)


# ============================================================================
# TEXT FIELD
# ============================================================================
class _Edit(QLineEdit):
    focusChanged = Signal()

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.focusChanged.emit()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.focusChanged.emit()


class TextField(QWidget):
    """
    30px text input (96px, or 268px when `wide`). Border is accent when
    dirty and error-coloured when invalid; the focus ring follows suit.

    Signals:
        textChanged(str)
    """

    textChanged = Signal(str)

    def __init__(self, text: str = '', *, wide: bool = False, mono: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._dirty = False
        self._error = False
        self._edit = _Edit(self)
        self._edit.setFrame(False)
        self._edit.setStyleSheet(
            'QLineEdit { background: transparent; border: none; padding: 0; min-height: 0; }')
        self._edit.setFont(Font.mono(12) if mono else Font.ui(13))
        self._edit.setText(text)
        self._edit.textChanged.connect(self.textChanged)
        self._edit.focusChanged.connect(self.update)
        self._edit.setGeometry(_M + 10, _M, 0, Spacing.ROW_HEIGHT)
        self.setFocusProxy(self._edit)
        w = (Spacing.CONTROL_WIDTH if wide else 96) + 2 * _M
        self.setFixedSize(w, Spacing.ROW_HEIGHT + 2 * _M)
        self._edit.setGeometry(_M + 10, _M, w - 2 * _M - 20, Spacing.ROW_HEIGHT)

    def text(self) -> str:
        return self._edit.text()

    def setText(self, text: str) -> None:
        self._edit.setText(text)

    def set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.update()

    def set_error(self, error: bool) -> None:
        self._error = error
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = body_rect(self)
        key = 'error' if self._error else ('accent' if self._dirty else 'border_2')
        p.setBrush(color('field'))
        p.setPen(QPen(color(key), 1))
        p.drawRoundedRect(r.adjusted(.5, .5, -.5, -.5), Radius.CONTROL, Radius.CONTROL)
        if self._edit.hasFocus() or self.property('ringPreview'):
            draw_focus_ring(p, r, Radius.CONTROL, 'error' if self._error else 'accent')


# ============================================================================
# BUTTON
# ============================================================================
class Button(QAbstractButton):
    """
    30px outlined button (e.g. "Reveal", "Console"). `primary=True` fills it
    with the accent. Space/Enter activate it.
    """

    def __init__(self, text: str = '', *, primary: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self._primary = primary
        self._hover = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(Font.ui(12))
        return QSize(fm.horizontalAdvance(self.text()) + 2 * 12 + 2 * _M, Spacing.ROW_HEIGHT + 2 * _M)

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() in _ACTIVATE_KEYS:
            if not e.isAutoRepeat():
                self.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:
        if e.key() in _ACTIVATE_KEYS:
            e.accept()
            return
        super().keyReleaseEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = body_rect(self)
        on = self.isEnabled()
        if self._primary:
            p.setBrush(color('accent') if on else color('accent_2'))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, Radius.CONTROL, Radius.CONTROL)
            fg = color('on_accent')
        else:
            hot = (self._hover or self.isDown()) and on
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color('accent' if hot else 'border_2'), 1))
            p.drawRoundedRect(r.adjusted(.5, .5, -.5, -.5), Radius.CONTROL, Radius.CONTROL)
            fg = color('accent' if hot else ('text' if on else 'faint'))
        p.setFont(Font.ui(12, 500 if self._primary else 400))
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        if wants_ring(self):
            draw_focus_ring(p, r, Radius.CONTROL)
