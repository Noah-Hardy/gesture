#!/usr/bin/env python3
"""
Main Window

The single Gesture window: toolbar, navigation rail, pane stack, pending-change
bar slot and console drawer, with the responsive behaviour from the handoff
(Handoff Specs section 02):

    >= 1180 px   comfortable  everything visible
    1024..1179   narrow       the OSC column collapses to a 14px strip (Live pane)
    < 1024       minimum      rail goes icon-only (52px), console closes

The window owns the layout *mode* and publishes it (`layout_mode`,
`layoutModeChanged`); the Live pane (#66) reacts to it for its OSC column.
Start/Stop and the fps readout live at fixed positions in the toolbar and never
move as the window is resized.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import subprocess
import sys
from typing import Optional

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QFontMetrics, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
    QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from src import docs
from src.config import Config

from . import menus
from .engine import EngineController
from .icon import paint_mark
from .overlays import about as overlay_about
from .overlays import faults as overlay_faults
from .overlays import first_run as overlay_first_run
from .overlays import update as overlay_update
from .panes import PANES
from .tokens import Font, Radius, color
from .widgets import Button

# ============================================================================
# CONSTANTS
# ============================================================================
DEFAULT_SIZE = (1240, 816)
MIN_SIZE = (980, 620)

TOOLBAR_HEIGHT = 76
RAIL_WIDTH = 196
RAIL_WIDTH_COLLAPSED = 52
RAIL_ITEM_HEIGHT = 32
RAIL_ACTIVE = '#232830'          # active rail item fill
RAIL_HOVER = '#20242C'
MARKER_WIDTH = 4

BREAKPOINT_COMFORTABLE = 1180    # >= : comfortable
BREAKPOINT_NARROW = 1024         # >= : narrow, below: minimum
MODES = ('comfortable', 'narrow', 'minimum')

CONSOLE_MIN = 90
CONSOLE_MAX = 360
CONSOLE_DEFAULT = 160
STAGE_MIN = 240                  # the stage never shrinks below this
CONSOLE_MAX_LINES = 3000
SAVE_DEBOUNCE_MS = 600

RUN_BUTTON_WIDTH = 116
STATS_WIDTH = 132

TOOLBAR_TOP = '#22262F'
TOOLBAR_BOTTOM = '#1E2129'


def layout_mode_for(width: int) -> str:
    """
    Layout mode for a window width.

    Args:
        width: Window width in px.

    Returns:
        'comfortable' (>= 1180), 'narrow' (1024-1179) or 'minimum' (< 1024).
    """
    if width >= BREAKPOINT_COMFORTABLE:
        return 'comfortable'
    if width >= BREAKPOINT_NARROW:
        return 'narrow'
    return 'minimum'


def _aa(p: QPainter) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)


# ============================================================================
# TOOLBAR WIDGETS
# ============================================================================
class RunButton(QAbstractButton):
    """
    Start/Stop button. Accent fill when stopped; error fill with a glowing dot
    while the engine runs. Fixed size, so its position never depends on the label.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = 'stopped'
        self._hover = False
        self.setText('Start')
        self.setFixedSize(RUN_BUTTON_WIDTH, 30)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Start or stop the engine')

    def set_state(self, state: str) -> None:
        """Show the engine state: stopped | starting | running | stopping."""
        self._state = state
        self.setText({'stopped': 'Start', 'starting': 'Stop', 'running': 'Stop',
                      'stopping': 'Stopping'}.get(state, 'Start'))
        self.setEnabled(state != 'stopping')
        self.update()

    def state(self) -> str:
        """Engine state last shown."""
        return self._state

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not e.isAutoRepeat():
                self.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        live = self._state in ('starting', 'running', 'stopping')
        base = color('error' if live else 'accent')
        if not self.isEnabled():
            base.setAlphaF(0.55)
        elif self._hover or self.isDown():
            base = base.lighter(112)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(r, Radius.CONTROL, Radius.CONTROL)
        fg = color('on_accent')
        dot_x = r.left() + 17
        if live:  # glowing dot
            glow = QRadialGradient(dot_x, r.center().y(), 9)
            glow.setColorAt(0, QColor(255, 255, 255, 150))
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(glow)
            p.drawEllipse(QRectF(dot_x - 9, r.center().y() - 9, 18, 18))
            p.setBrush(fg)
            p.drawEllipse(QRectF(dot_x - 3, r.center().y() - 3, 6, 6))
        p.setFont(Font.ui(13, 500))
        p.setPen(fg)
        text_rect = r.adjusted(26 if live else 0, 0, 0, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            for inset, key in ((1.5, 'bg_deep'), (3.5, 'accent')):
                pen = QPen(color(key))
                pen.setWidthF(2)
                p.setPen(pen)
                p.drawRoundedRect(r.adjusted(-inset, -inset, inset, inset),
                                  Radius.CONTROL + inset, Radius.CONTROL + inset)


class RestartRequiredChip(QWidget):
    """'Restart required' toolbar chip: warn dot and text, 1px warn outline at 33% alpha."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = 'Restart required'
        fm = QFontMetrics(Font.ui(11))
        self.setFixedSize(fm.horizontalAdvance(self._text) + 36, 26)
        self.setAccessibleName('Restart required')

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color('warn', 0.33), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 7, 7)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('warn'))
        p.drawEllipse(QRectF(11.5, self.height() / 2 - 2.5, 5, 5))
        p.setFont(Font.ui(11))
        p.setPen(color('warn'))
        p.drawText(QRectF(24, 0, self.width() - 24, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class _Divider(QWidget):
    """1px vertical toolbar divider fading out at both ends."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(1, 22)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(.5, color('border_2'))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), g)


class Toolbar(QFrame):
    """
    The 76px toolbar. Left to right: Start/Stop, MODE / IN / OUT readouts, a
    stretch, the Restart-required chip, the fps / latency readout, a divider
    and the Console toggle. Everything is fixed-size except the stretch, so the
    run button keeps its x and the fps readout its distance from the right edge.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('toolbar')
        self.setFixedHeight(TOOLBAR_HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(14)

        self.run = RunButton()
        lay.addWidget(self.run)

        self.readouts = {}
        lay.addSpacing(8)
        for key in ('MODE', 'IN', 'OUT'):
            label = QLabel()
            label.setFont(Font.ui(12))
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setStyleSheet('background: transparent;')
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.readouts[key] = label
            lay.addWidget(label)
            lay.addSpacing(4)
        lay.addStretch(1)

        self.chip = RestartRequiredChip()
        self.chip.setVisible(False)
        lay.addWidget(self.chip)

        self.stats = QLabel('-- fps · -- ms')
        self.stats.setFont(Font.ui(12))
        self.stats.setFixedWidth(STATS_WIDTH)
        self.stats.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.stats.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
        self.stats.setAccessibleName('Frame rate and latency')
        lay.addWidget(self.stats)

        lay.addWidget(_Divider())
        self.console_button = Button('Console')
        self.console_button.setAccessibleName('Toggle console')
        lay.addWidget(self.console_button)

    def set_readout(self, key: str, value: str) -> None:
        """Set the MODE / IN / OUT value text."""
        faint, text = color('faint').name(), color('text').name()
        safe = value.replace('&', '&amp;').replace('<', '&lt;')
        self.readouts[key].setText(
            f'<span style="color:{faint}">{key}</span>&nbsp;&nbsp;'
            f'<span style="color:{text}; font-weight:500">{safe}</span>')

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(TOOLBAR_TOP))
        g.setColorAt(1, QColor(TOOLBAR_BOTTOM))
        p.fillRect(self.rect(), g)
        p.fillRect(0, self.height() - 1, self.width(), 1, color('border'))


# ============================================================================
# RAIL
# ============================================================================
def _draw_glyph(p: QPainter, key: str, r: QRectF, fg: QColor) -> None:
    """Draw the 16px icon for a pane (used when the rail is icon-only)."""
    p.save()
    pen = QPen(fg, 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    c = r.center()
    if key == 'live':
        p.drawEllipse(c, 6.5, 6.5)
        p.setBrush(fg)
        p.drawEllipse(c, 2.5, 2.5)
    elif key == 'input':
        p.drawRoundedRect(QRectF(c.x() - 7, c.y() - 4.5, 14, 10), 2.5, 2.5)
        p.drawEllipse(c, 2.6, 2.6)
    elif key == 'osc':
        for dx in (-5, 0, 5):
            path = QPainterPath()
            path.moveTo(c.x() + dx - 2, c.y() - 4)
            path.lineTo(c.x() + dx + 2, c.y())
            path.lineTo(c.x() + dx - 2, c.y() + 4)
            p.drawPath(path)
    elif key == 'tracking':
        p.drawEllipse(c.x(), c.y() - 5.2, 2.6, 2.6)
        p.drawLine(c.x(), c.y() - 2.5, c.x(), c.y() + 2.5)
        p.drawLine(c.x() - 5, c.y() - 1, c.x() + 5, c.y() - 1)
        p.drawLine(c.x(), c.y() + 2.5, c.x() - 3.5, c.y() + 7)
        p.drawLine(c.x(), c.y() + 2.5, c.x() + 3.5, c.y() + 7)
    elif key == 'preview':
        p.drawRoundedRect(QRectF(c.x() - 7, c.y() - 5, 14, 10), 2, 2)
        p.drawLine(c.x() - 4, c.y() + 2, c.x() - 1, c.y() - 1.5)
        p.drawLine(c.x() - 1, c.y() - 1.5, c.x() + 4, c.y() + 2)
    elif key == 'advanced':
        for i, (dy, kx) in enumerate(((-4.5, -2.5), (0, 3), (4.5, -1))):
            p.drawLine(c.x() - 7, c.y() + dy, c.x() + 7, c.y() + dy)
            p.setBrush(color('rail'))
            p.drawEllipse(c.x() + kx - 2, c.y() + dy - 2, 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
    else:  # help
        p.setPen(fg)
        p.setFont(Font.ui(14, 500))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, '?')
    p.restore()


class RailItem(QAbstractButton):
    """One pane entry in the rail: 4px accent marker, label, active fill."""

    def __init__(self, key: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.setText(label)
        self._active = False
        self._hover = False
        self._collapsed = False
        self.setFixedHeight(RAIL_ITEM_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)

    def set_active(self, active: bool) -> None:
        """Mark the item as the current pane."""
        self._active = active
        self.update()

    def is_active(self) -> bool:
        """True when this is the current pane."""
        return self._active

    def set_collapsed(self, collapsed: bool) -> None:
        """Icon-only mode with a hover label (tooltip)."""
        self._collapsed = collapsed
        self.setToolTip(self.text() if collapsed else '')
        self.update()

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not e.isAutoRepeat():
                self.click()
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = QRectF(self.rect())
        if self._active or self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(RAIL_ACTIVE if self._active else RAIL_HOVER))
            p.drawRoundedRect(r, Radius.CONTROL, Radius.CONTROL)
        if self._active:
            p.setBrush(color('accent'))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(0, r.center().y() - 7, MARKER_WIDTH, 14), 2, 2)
        fg = color('text' if self._active else 'dim')
        if self._collapsed:
            _draw_glyph(p, self.key, QRectF(r.center().x() - 8, r.center().y() - 8, 16, 16), fg)
        else:
            p.setFont(Font.ui(13))
            p.setPen(fg)
            p.drawText(QRectF(18, 0, r.width() - 18, r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(color('accent'))
            pen.setWidthF(2)
            p.setPen(pen)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), Radius.CONTROL, Radius.CONTROL)


class RailMark(QWidget):
    """The 26px icon mark, painted from src/icon_geometry.py."""

    def __init__(self, size: int = 26, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        paint_mark(p, QRectF(self.rect()), 'small')


class UpdateCard(QWidget):
    """Rail-bottom card announcing an available update; a dot when the rail is collapsed."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = ''
        self._sub = 'Verified & ready to install'
        self._collapsed = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        self.setVisible(False)

    def set_release(self, release) -> None:
        """Show the card for a release (anything with `.version`), or hide it for None."""
        if release is None:
            self.setVisible(False)
            return
        self._title = '{} available'.format(getattr(release, 'version', '') or 'Update')
        self.setToolTip(self._title)
        self.setVisible(True)
        self.update()

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse to a dot (rail is icon-only)."""
        self._collapsed = collapsed
        self.setFixedHeight(24 if collapsed else 58)
        self.update()

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        if self._collapsed:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent'))
            p.drawEllipse(QRectF(self.width() / 2 - 4, self.height() / 2 - 4, 8, 8))
            return
        r = QRectF(self.rect()).adjusted(4.5, .5, -4.5, -4.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color('accent' if self._hover else 'border_2'), 1))
        p.drawRoundedRect(r, Radius.CONTROL, Radius.CONTROL)
        p.setFont(Font.ui(11, 500))
        p.setPen(color('accent'))
        p.drawText(QRectF(r.left() + 10, r.top() + 9, r.width() - 20, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._title)
        p.setFont(Font.ui(11))
        p.setPen(color('dim'))
        p.drawText(QRectF(r.left() + 10, r.top() + 26, r.width() - 20, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._sub)


class Rail(QFrame):
    """
    The navigation rail: mark, name and version, the pane items, and the update
    card at the bottom. 196px wide, 52px and icon-only when collapsed.

    Signals:
        paneRequested(str): an item was clicked.
        updateClicked(): the update card was clicked.
    """

    paneRequested = Signal(str)
    updateClicked = Signal()

    def __init__(self, version: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('rail')
        self.setFixedWidth(RAIL_WIDTH)
        self._collapsed = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 14, 10, 14)
        lay.setSpacing(2)

        self._head = QWidget()
        head = QHBoxLayout(self._head)
        head.setContentsMargins(8, 4, 8, 16)
        head.setSpacing(10)
        self.mark = RailMark()
        head.addWidget(self.mark)
        self._names = QWidget()
        names = QVBoxLayout(self._names)
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        self.title = QLabel('Gesture')
        self.title.setFont(Font.ui(13, 500))
        self.version = QLabel(version)
        self.version.setFont(Font.ui(10))
        self.version.setStyleSheet(f"color: {color('faint').name()};")
        for lbl in (self.title, self.version):
            lbl.setStyleSheet(lbl.styleSheet() + 'background: transparent;')
            names.addWidget(lbl)
        head.addWidget(self._names)
        head.addStretch(1)
        lay.addWidget(self._head)

        self.items = {}
        for key, label, _module in PANES:
            item = RailItem(key, label)
            item.clicked.connect(lambda _=False, k=key: self.paneRequested.emit(k))
            self.items[key] = item
            lay.addWidget(item)

        lay.addStretch(1)
        self.update_card = UpdateCard()
        self.update_card.clicked.connect(self.updateClicked)
        lay.addWidget(self.update_card)

    def set_current(self, key: str) -> None:
        """Highlight the item for `key`."""
        for k, item in self.items.items():
            item.set_active(k == key)

    def set_collapsed(self, collapsed: bool) -> None:
        """Switch between the 196px labelled rail and the 52px icon-only rail."""
        self._collapsed = collapsed
        self.setFixedWidth(RAIL_WIDTH_COLLAPSED if collapsed else RAIL_WIDTH)
        self._names.setVisible(not collapsed)
        self.layout().setContentsMargins(6 if collapsed else 10, 14, 6 if collapsed else 10, 14)
        self._head.layout().setContentsMargins(7 if collapsed else 8, 4, 0 if collapsed else 8, 16)
        for item in self.items.values():
            item.set_collapsed(collapsed)
        self.update_card.set_collapsed(collapsed)

    def is_collapsed(self) -> bool:
        """True while icon-only."""
        return self._collapsed

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), color('rail'))
        p.fillRect(self.width() - 1, 0, 1, self.height(), color('border'))


# ============================================================================
# CONSOLE DRAWER
# ============================================================================
class _Link(QLabel):
    """Text link: dim, accent on hover. Emits `clicked`."""

    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setFont(Font.ui(11))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set(False)

    def _set(self, hot: bool) -> None:
        self.setStyleSheet(f"color: {color('accent' if hot else 'dim').name()}; background: transparent;")

    def enterEvent(self, _e) -> None:
        self._set(True)

    def leaveEvent(self, _e) -> None:
        self._set(False)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class ConsoleDrawer(QFrame):
    """
    Console drawer: a 34px header ("Engine output - stdout - SIGINT on stop"
    and a Hide link) over a read-only monospace log, capped at 3000 lines.

    Signals:
        hideRequested(): the Hide link was clicked.
    """

    hideRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('console')
        self.setMinimumHeight(CONSOLE_MIN)
        self.setMaximumHeight(CONSOLE_MAX)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(34)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(12)
        title = QLabel('ENGINE OUTPUT')
        tf = Font.ui(11)
        tf.setLetterSpacing(tf.SpacingType.AbsoluteSpacing, 1.1)
        title.setFont(tf)
        title.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
        sub = QLabel('stdout · SIGINT on stop')
        sub.setFont(Font.ui(11))
        sub.setStyleSheet(f"color: {color('faint').name()}; background: transparent;")
        self.hide_link = _Link('Hide')
        self.hide_link.clicked.connect(self.hideRequested)
        h.addWidget(title)
        h.addWidget(sub)
        h.addStretch(1)
        h.addWidget(self.hide_link)
        lay.addWidget(header)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(CONSOLE_MAX_LINES)
        self.log.setFont(Font.mono(11))
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setStyleSheet(
            f"QPlainTextEdit {{ background: {color('console').name()}; border: none;"
            f" color: {color('text_2').name()}; padding: 6px 16px; }}")
        lay.addWidget(self.log, 1)

    def append_line(self, text: str) -> None:
        """Append one line, keeping the view pinned to the bottom if it was there."""
        bar = self.log.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.log.appendPlainText(text)
        if at_end:
            bar.setValue(bar.maximum())

    def text(self) -> str:
        """The whole log as plain text."""
        return self.log.toPlainText()

    def clear(self) -> None:
        """Empty the log."""
        self.log.clear()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), color('console'))
        p.fillRect(0, 0, self.width(), 1, color('border'))
        p.fillRect(0, 34, self.width(), 1, color('hairline'))


# ============================================================================
# OVERLAY HOST / TOAST
# ============================================================================
class OverlayHost(QWidget):
    """
    Full-window layer overlay cards are parented on. It dims the shell and
    swallows clicks while any child card is shown, and is invisible otherwise.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('overlayHost')
        self.hide()

    def sync(self) -> None:
        """Show the dim layer iff at least one child card is shown."""
        any_card = any(not c.isHidden() for c in self.children() if isinstance(c, QWidget))
        if any_card != self.isVisible():
            self.setVisible(any_card)
        if any_card:
            self.raise_()

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.ChildAdded:
            child = e.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)
                QTimer.singleShot(0, self.sync)
        elif e.type() == QEvent.Type.ChildRemoved:
            QTimer.singleShot(0, self.sync)
        return super().event(e)

    def eventFilter(self, obj, e) -> bool:
        if e.type() in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self.sync()
        return False

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(8, 9, 12, 170))

    def mousePressEvent(self, e) -> None:
        e.accept()


class Toast(QFrame):
    """Transient confirmation pill, bottom centre of the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('toast')
        self._label = QLabel()
        self._label.setFont(Font.ui(12.5))
        self._label.setStyleSheet('background: transparent;')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 9, 16, 9)
        lay.setSpacing(9)
        self._dot = QWidget()
        self._dot.setFixedSize(6, 6)
        self._dot.setStyleSheet(f"background: {color('accent').name()}; border-radius: 3px;")
        lay.addWidget(self._dot)
        lay.addWidget(self._label)
        self.setStyleSheet(
            f"QFrame#toast {{ background: #141A17; border: 1px solid {color('accent_2').name()};"
            f" border-radius: 9px; }}")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_text(self, text: str, ms: int = 2800) -> None:
        """Show `text` for `ms` milliseconds."""
        self._label.setText(text)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self._timer.start(ms)

    def place(self) -> None:
        """Centre horizontally, 26px above the bottom of the parent."""
        p = self.parentWidget()
        if p is not None:
            self.move((p.width() - self.width()) // 2, p.height() - self.height() - 26)


# ============================================================================
# MAIN WINDOW
# ============================================================================
class MainWindow(QMainWindow):
    """
    The Gesture window.

    Public surface (the UI architecture contract, 0.4.0):
        config, engine, edits, actions, layout_mode, show_pane(), current_pane(),
        overlay_host(), toast(), set_pending_bar(), set_update_available(),
        set_restart_required(), set_stats(), refresh_readouts().

    Signals:
        layoutModeChanged(str): 'comfortable' | 'narrow' | 'minimum'.
        paneChanged(str): the visible pane key changed.
        updateCardClicked(): the rail's update card was clicked.
        consoleToggled(bool): the console drawer opened or closed.
    """

    layoutModeChanged = Signal(str)
    paneChanged = Signal(str)
    updateCardClicked = Signal()
    consoleToggled = Signal(bool)

    def __init__(self, config: Config, engine: EngineController | None = None):
        super().__init__()
        self.config = config
        self.engine = engine or EngineController(config, self)
        self.edits = None                      # EditModel (#64)
        self.actions: dict[str, QAction] = {}
        self._layout_mode = layout_mode_for(DEFAULT_SIZE[0])
        self._pane_keys = [k for k, _, _ in PANES]
        self._current = self._pane_keys[0]

        self.setWindowTitle('Gesture')
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*DEFAULT_SIZE)

        self._console_open = bool(config.get('ui', 'console_open', True))
        self._console_height = self._clamp_console(config.get('ui', 'console_height', CONSOLE_DEFAULT))
        self._console_auto_closed = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_config)

        self._build_ui()
        self._build_actions()
        self._wire_engine()

        self.refresh_readouts()
        self.set_stats(None, None)
        self.show_pane(self._current)
        self._apply_layout_mode(self._layout_mode, initial=True)

        for module in (overlay_about, overlay_update, overlay_first_run, overlay_faults, menus):
            module.install(self)

    # ------------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName('central')
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.toolbar = Toolbar()
        self.toolbar.run.clicked.connect(self.toggle_engine)
        self.toolbar.console_button.clicked.connect(self.toggle_console)
        outer.addWidget(self.toolbar)

        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        outer.addWidget(body, 1)

        self.rail = Rail(docs.app_version() or '')
        self.rail.paneRequested.connect(self.show_pane)
        self.rail.updateClicked.connect(self.updateCardClicked)
        body_lay.addWidget(self.rail)

        # Stage: pane stack + pending-bar slot, over the console, in a vertical splitter
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        self.splitter.setStyleSheet('QSplitter::handle { background: transparent; }')
        stage = QWidget()
        stage.setMinimumHeight(STAGE_MIN)
        stage_lay = QVBoxLayout(stage)
        stage_lay.setContentsMargins(0, 0, 0, 0)
        stage_lay.setSpacing(0)
        self.stack = QStackedWidget()
        stage_lay.addWidget(self.stack, 1)
        self._pending_slot = QWidget()
        self._pending_lay = QVBoxLayout(self._pending_slot)
        self._pending_lay.setContentsMargins(0, 0, 0, 0)
        self._pending_slot.setVisible(False)
        stage_lay.addWidget(self._pending_slot)
        self.splitter.addWidget(stage)

        self.console = ConsoleDrawer()
        self.console.hideRequested.connect(self.toggle_console)
        self.splitter.addWidget(self.console)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        body_lay.addWidget(self.splitter, 1)

        self._panes = {}
        for key, _label, module in PANES:
            widget = module.create(self)
            self._panes[key] = widget
            self.stack.addWidget(widget)

        self._overlay_host = OverlayHost(central)
        self._overlay_host.setGeometry(central.rect())
        self._toast = Toast(central)
        central.installEventFilter(self)
        self.console.setVisible(self._console_open)

    def _build_actions(self) -> None:
        def act(key: str, text: str, shortcut: str | None = None, slot=None) -> QAction:
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            if slot is not None:
                a.triggered.connect(slot)
            self.addAction(a)
            self.actions[key] = a
            return a

        act('start_stop', 'Start', 'Ctrl+Return', self.toggle_engine)
        act('restart', 'Restart Engine', 'Ctrl+R', self.engine.restart)
        act('apply', 'Apply Pending Changes', 'Ctrl+S')
        act('revert', 'Revert Changes', 'Ctrl+Z')
        act('settings', 'Settings...', 'Ctrl+,', lambda: self.show_pane('input'))
        act('toggle_console', 'Toggle Console', 'Ctrl+Shift+C', self.toggle_console)
        act('toggle_osc', 'Toggle OSC Activity', 'Ctrl+Shift+O')
        act('next_pane', 'Next Pane', 'Ctrl+]', lambda: self.step_pane(1))
        act('prev_pane', 'Previous Pane', 'Ctrl+[', lambda: self.step_pane(-1))
        act('reveal_config', 'Reveal config.json', 'Ctrl+Shift+,', self.reveal_config)
        act('copy_console', 'Copy Console Output', 'Ctrl+Shift+L', self.copy_console)
        act('docs', 'Documentation', 'Ctrl+?')
        act('check_updates', 'Check for Updates...')
        act('about', 'About Gesture')
        act('setup', 'Set Up...')
        act('quit', 'Quit Gesture', 'Ctrl+Q', self.close)
        self.actions['restart'].setEnabled(False)

    def _wire_engine(self) -> None:
        self.engine.stateChanged.connect(self._on_engine_state)
        self.engine.output.connect(self.console.append_line)
        self.engine.statusReceived.connect(self._on_status)

    # ------------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------------
    def toggle_engine(self) -> None:
        """Start the engine, or stop it if it is running or starting."""
        if self.engine.is_running():
            self.engine.stop()
        else:
            self.engine.start()

    def _on_engine_state(self, state: str) -> None:
        self.toolbar.run.set_state(state)
        self.actions['start_stop'].setText('Stop' if state in ('starting', 'running') else 'Start')
        self.actions['start_stop'].setEnabled(state != 'stopping')
        self.actions['restart'].setEnabled(state in ('starting', 'running'))
        if state == 'stopped':
            self.set_stats(None, None)

    def _on_status(self, msg: dict) -> None:
        self.set_stats(msg.get('fps'), msg.get('latency_ms'))

    # ------------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------------
    def refresh_readouts(self) -> None:
        """Re-read MODE / IN / OUT from the Config (call after config changes)."""
        c = self.config
        self.toolbar.set_readout('MODE', str(c.get('ui', 'mode', 'all')))
        if c.get('camera', 'use_ndi', False):
            source = str(c.get('camera', 'ndi_source', '') or '') or 'NDI'
        else:
            source = 'Camera {}'.format(c.get('camera', 'device_id', 0))
        self.toolbar.set_readout('IN', source)
        self.toolbar.set_readout('OUT', '{}:{}'.format(c.get('osc', 'host', ''), c.get('osc', 'port', '')))

    def set_stats(self, fps, latency_ms) -> None:
        """
        Update the right-aligned readout.

        Args:
            fps: Frames per second, or None for "--".
            latency_ms: Latency in milliseconds, or None for "--".
        """
        f = '--' if fps is None else '{:.0f}'.format(fps)
        m = '--' if latency_ms is None else '{:.0f}'.format(latency_ms)
        self.toolbar.stats.setText('{} fps · {} ms'.format(f, m))

    def set_restart_required(self, required: bool) -> None:
        """Show or hide the toolbar's 'Restart required' chip."""
        self.toolbar.chip.setVisible(bool(required))

    # ------------------------------------------------------------------------
    # Panes
    # ------------------------------------------------------------------------
    def current_pane(self) -> str:
        """Key of the visible pane."""
        return self._current

    def pane(self, key: str) -> QWidget:
        """The widget for pane `key`."""
        return self._panes[key]

    def show_pane(self, key: str, anchor: str | None = None) -> None:
        """
        Switch to a pane.

        Args:
            key: live, input, osc, tracking, preview, advanced or help.
            anchor: Optional section/row anchor; passed to the pane's
                `scroll_to_anchor(anchor)` if it defines one.
        """
        if key not in self._panes:
            return
        changed = key != self._current
        self._current = key
        self.stack.setCurrentWidget(self._panes[key])
        self.rail.set_current(key)
        if anchor:
            hook = getattr(self._panes[key], 'scroll_to_anchor', None)
            if callable(hook):
                hook(anchor)
        if changed:
            self.paneChanged.emit(key)

    def step_pane(self, delta: int) -> None:
        """Move to the next (+1) or previous (-1) pane, wrapping around."""
        i = (self._pane_keys.index(self._current) + delta) % len(self._pane_keys)
        self.show_pane(self._pane_keys[i])

    # ------------------------------------------------------------------------
    # Slots for other workers
    # ------------------------------------------------------------------------
    def set_pending_bar(self, widget: Optional[QWidget]) -> None:
        """
        Put `widget` in the pending-change slot above the console.

        Args:
            widget: The bar (#64), or None to empty the slot.
        """
        while self._pending_lay.count():
            item = self._pending_lay.takeAt(0)
            if item.widget() is not None and item.widget() is not widget:
                item.widget().setParent(None)
        if widget is not None:
            self._pending_lay.addWidget(widget)
        self._pending_slot.setVisible(widget is not None)

    def set_update_available(self, release) -> None:
        """
        Show or hide the rail's update card.

        Args:
            release: A `src.updater.Release` (or anything with `.version`), or None.
        """
        self.rail.update_card.set_release(release)

    def overlay_host(self) -> QWidget:
        """The full-window layer to parent overlay cards on; it dims the shell while one is shown."""
        return self._overlay_host

    def toast(self, text: str, ms: int = 2800) -> None:
        """Show a transient confirmation for `ms` milliseconds."""
        self._toast.show_text(text, ms)

    # ------------------------------------------------------------------------
    # Layout modes
    # ------------------------------------------------------------------------
    @property
    def layout_mode(self) -> str:
        """Current layout mode: 'comfortable', 'narrow' or 'minimum'."""
        return self._layout_mode

    def _apply_layout_mode(self, mode: str, initial: bool = False) -> None:
        changed = mode != self._layout_mode
        self._layout_mode = mode
        minimum = mode == 'minimum'
        self.rail.set_collapsed(minimum)
        if minimum:
            if self._console_open:
                self._console_auto_closed = True
                self._set_console_visible(False)
        elif self._console_auto_closed:
            self._console_auto_closed = False
            self._set_console_visible(True)
        if changed and not initial:
            self.layoutModeChanged.emit(mode)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        mode = layout_mode_for(self.width())
        if mode != self._layout_mode:
            self._apply_layout_mode(mode)
        self._sync_overlay()
        self._fit_console()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._fit_console()

    def eventFilter(self, obj, e) -> bool:
        if obj is self.centralWidget() and e.type() == QEvent.Type.Resize:
            self._sync_overlay()
        return super().eventFilter(obj, e)

    def _sync_overlay(self) -> None:
        central = self.centralWidget()
        if central is not None:
            self._overlay_host.setGeometry(central.rect())
            self._toast.place()

    # ------------------------------------------------------------------------
    # Console drawer
    # ------------------------------------------------------------------------
    @staticmethod
    def _clamp_console(value) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = CONSOLE_DEFAULT
        return max(CONSOLE_MIN, min(CONSOLE_MAX, value))

    def console_open(self) -> bool:
        """True while the console drawer is shown."""
        return self.console.isVisibleTo(self)

    def console_height(self) -> int:
        """The requested drawer height in px (90-360); the shown height may be less in a short window."""
        return self._console_height

    def set_console_height(self, height: int) -> None:
        """Set and persist the drawer height (clamped to 90-360)."""
        self._console_height = self._clamp_console(height)
        self.config.set('ui', 'console_height', self._console_height)
        self._fit_console()
        self._save_timer.start()

    def toggle_console(self) -> None:
        """Open or close the console drawer (a user choice, persisted)."""
        self._console_auto_closed = False
        self.set_console_open(not self._console_open)

    def set_console_open(self, open_: bool) -> None:
        """Open or close the console drawer and persist the choice."""
        self._console_auto_closed = False
        self._console_open = bool(open_)
        self.config.set('ui', 'console_open', self._console_open)
        self._set_console_visible(self._console_open)
        self._save_timer.start()

    def _set_console_visible(self, visible: bool) -> None:
        was = self.console.isVisibleTo(self)
        self.console.setVisible(visible)
        if visible:
            self._fit_console()
        if was != visible:
            self.consoleToggled.emit(visible)

    def _fit_console(self) -> None:
        """Size the splitter: the drawer gets its height, but the stage keeps 240px."""
        if not self.console.isVisibleTo(self):
            return
        total = self.splitter.height()
        if total <= 0:
            return
        h = max(CONSOLE_MIN, min(self._console_height, total - STAGE_MIN))
        self.splitter.setSizes([total - h, h])

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] > 0:
            self._console_height = self._clamp_console(sizes[1])
            self.config.set('ui', 'console_height', self._console_height)
            self._save_timer.start()

    def _save_config(self) -> None:
        self.config.save()

    def copy_console(self) -> None:
        """Copy the console text to the clipboard."""
        QApplication.clipboard().setText(self.console.text())
        self.toast('Console output copied')

    def reveal_config(self) -> None:
        """Reveal config.json in Finder."""
        path = self.config.config_file
        try:
            if sys.platform == 'darwin':
                subprocess.run(['/usr/bin/open', '-R', path], check=False)
            else:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                import os
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(os.path.abspath(path))))
        except OSError as exc:
            self.toast('Could not reveal config.json: {}'.format(exc))

    # ------------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------------
    def closeEvent(self, e) -> None:
        # Until the status item (#71) exists, quitting stops the engine cleanly
        self._save_timer.stop()
        self.engine.stop_and_wait()
        self.config.set('ui', 'console_open', self._console_open)
        self.config.set('ui', 'console_height', self._console_height)
        self.config.save()
        e.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def sizeHint(self) -> QSize:
        return QSize(*DEFAULT_SIZE)
