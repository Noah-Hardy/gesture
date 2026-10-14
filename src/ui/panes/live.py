#!/usr/bin/env python3
"""
Live Pane

The default pane (#66): the tracked preview stage, four meters and the OSC
activity column. Everything is driven by the engine's status channel
(`engine.statusReceived`) and preview frames (`engine.frameReady`); the pane
never reads the engine process directly.

Responsive behaviour (#63): the OSC column is docked at 322px in the
comfortable layout, collapses to a 14px pulsing strip when narrow (a click
opens it over the stage), and at the minimum width the meters move into it.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractButton, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..osc_activity import ActivityTracker, format_uptime, landmark_points, predict_families
from ..tokens import Font, Radius, color
from ..widgets import Meter, Sparkline

TITLE = 'Live'
BLURB = 'Preview, meters and OSC activity.'

# ============================================================================
# CONSTANTS
# ============================================================================
COLUMN_WIDTH = 322
STRIP_WIDTH = 14
PAD = 18
GRID_STEP = 56
PULSE_MS = 120               # Dot animation tick
SPARK_EVERY = 4              # Sparkline sample every N ticks (~2 Hz)
QUEUE_WARN = 0.75            # Queue fullness that turns the readout warn
DROP_WARN_SECONDS = 30.0     # A rising drop count stays warn this long
DEFAULT_FPS_TARGET = 30


def _cfg(window, section: str, key: str, default=None):
    return window.config.get(section, key, default)


# ============================================================================
# CHIPS
# ============================================================================
class Chip(QAbstractButton):
    """
    Stage chip: dark translucent pill with 11px text. Static (a label) unless
    `toggle` is set, in which case it is a checkable button.

    Args:
        text: Chip text.
        tone: Palette key for the text colour.
        toggle: True for a Mirror / World coords style toggle.
    """

    def __init__(self, text: str = '', tone: str = 'dim', toggle: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._tone = tone
        self._toggle = toggle
        self.setText(text)
        self.setCheckable(toggle)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus if toggle else Qt.FocusPolicy.NoFocus)
        if toggle:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFont(Font.ui(11))

    def set_tone(self, tone: str) -> None:
        """Change the text colour (palette key)."""
        self._tone = tone
        self.update()

    def sizeHint(self) -> QSize:
        w = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(w + 18, 24)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        on = self._toggle and self.isChecked()
        p.setBrush(QColor(13, 15, 19, 153))
        border = 'accent_2' if on or self.hasFocus() else 'border'
        p.setPen(QPen(color(border), 1))
        p.drawRoundedRect(r, 6, 6)
        p.setFont(self.font())
        p.setPen(color('accent' if on else self._tone))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


# ============================================================================
# STAGE
# ============================================================================
class Stage(QWidget):
    """
    The preview stage: radial background and grid, the frame aspect-fit
    (letterboxed) in the middle, chips over it, and the optional stats lines.
    Frames arrive already mirrored by the engine.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('liveStage')
        self.setMinimumSize(240, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._frame: QImage | None = None
        self._stats: list[str] = []

        self.source_chip = Chip('', 'dim', parent=self)
        self.holistic_chip = Chip('holistic', 'accent', parent=self)
        self.mirror_chip = Chip('Mirror', 'dim', toggle=True, parent=self)
        self.world_chip = Chip('World coords', 'dim', toggle=True, parent=self)
        self.note = QLabel('preview only — not what is sent', self)
        self.note.setFont(Font.ui(11))
        self.note.setStyleSheet(f"color: {color('faint').name()}; background: transparent;")
        self.note.adjustSize()

    # -- content -------------------------------------------------------------
    def set_frame(self, image: QImage | None) -> None:
        """Show a preview frame (None clears it)."""
        self._frame = image
        self.update()

    def has_frame(self) -> bool:
        """True when a frame is being shown."""
        return self._frame is not None

    def set_stats(self, lines: list[str]) -> None:
        """Three monospace stats lines (empty list hides the overlay)."""
        self._stats = list(lines)
        self.update()

    def stats_lines(self) -> list[str]:
        """The stats lines currently drawn."""
        return list(self._stats)

    def set_source(self, text: str, holistic: bool) -> None:
        """Top-left chips: input description and the holistic chip."""
        self.source_chip.setText(text)
        self.source_chip.resize(self.source_chip.sizeHint())
        self.holistic_chip.setVisible(holistic)
        self._place()

    def frame_rect(self) -> QRectF:
        """Where the frame is drawn (aspect-fit inside the stage)."""
        f = self._frame
        r = QRectF(self.rect())
        if f is None or f.isNull():
            return r
        scale = min(r.width() / f.width(), r.height() / f.height())
        w, h = f.width() * scale, f.height() * scale
        return QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h)

    # -- layout --------------------------------------------------------------
    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place()

    def _place(self) -> None:
        for chip in (self.source_chip, self.holistic_chip, self.mirror_chip, self.world_chip):
            chip.resize(chip.sizeHint())
        x = 14
        self.source_chip.move(x, 12)
        self.holistic_chip.move(x + self.source_chip.width() + 8, 12)
        h = self.height()
        self.mirror_chip.move(14, h - 12 - self.mirror_chip.height())
        self.world_chip.move(14 + self.mirror_chip.width() + 6, h - 12 - self.world_chip.height())
        self.note.adjustSize()
        self.note.move(self.width() - 14 - self.note.width(), h - 12 - self.note.height())
        for w in (self.source_chip, self.holistic_chip, self.mirror_chip, self.world_chip, self.note):
            w.raise_()

    # -- painting ------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        # Design: radial-gradient(120% 100% at 50% 0%, stage_end -> stage)
        g = QRadialGradient(r.center().x(), 0, max(r.width() * 0.6, r.height()))
        g.setColorAt(0, color('stage_end'))
        g.setColorAt(1, color('stage'))
        p.setBrush(g)
        p.setPen(QPen(color('border'), 1))
        p.drawRoundedRect(r, Radius.CARD, Radius.CARD)
        p.save()
        p.setClipPath(self._clip(r))
        grid = QPen(QColor(255, 255, 255, 8), 1)
        p.setPen(grid)
        x = 0
        while x < self.width():
            p.drawLine(x, 0, x, self.height())
            x += GRID_STEP
        y = 0
        while y < self.height():
            p.drawLine(0, y, self.width(), y)
            y += GRID_STEP
        if self._frame is not None and not self._frame.isNull():
            p.drawImage(self.frame_rect(), self._frame)
        p.restore()
        if self._stats:
            self._paint_stats(p)

    @staticmethod
    def _clip(r: QRectF):
        path = QPainterPath()
        path.addRoundedRect(r, Radius.CARD, Radius.CARD)
        return path

    def _paint_stats(self, p: QPainter) -> None:
        f = Font.mono(12)
        p.setFont(f)
        line_h = 19
        x = 14
        y0 = 12 + 24 + 10
        for i, text in enumerate(self._stats):
            box = QRectF(x, y0 + i * line_h, 320, line_h)
            p.setPen(QColor(0, 0, 0, 220))
            p.drawText(box.translated(0, 1), Qt.AlignmentFlag.AlignVCenter, text)
            p.setPen(color('accent'))
            p.drawText(box, Qt.AlignmentFlag.AlignVCenter, text)


# ============================================================================
# OSC ACTIVITY
# ============================================================================
class ActivityRow(QWidget):
    """One address family: pulsing dot, monospace address, N/s and a sparkline."""

    def __init__(self, family: str, zebra: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.family = family
        self._zebra = zebra
        self._opacity = 0.18
        self._hist: deque[float] = deque([0.0] * Sparkline.BARS, maxlen=Sparkline.BARS)
        self._rate = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._dot = QWidget(self)
        self._dot.setFixedSize(5, 5)
        self._dot.paintEvent = self._paint_dot  # type: ignore[method-assign]
        self.address = QLabel(family)
        self.address.setFont(Font.mono(11))
        self.address.setStyleSheet(f"color: {color('text').name()}; background: transparent;")
        self.address.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.rate_label = QLabel('0/s')
        self.rate_label.setFont(Font.ui(10))
        self.rate_label.setStyleSheet(f"color: {color('faint').name()}; background: transparent;")
        top.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.address, 1)
        top.addWidget(self.rate_label)
        lay.addLayout(top)
        self.spark = Sparkline(self)
        lay.addWidget(self.spark)
        self.setFixedHeight(8 + 15 + 6 + Sparkline.HEIGHT + 8)

    def rate(self) -> float:
        """The last rate shown."""
        return self._rate

    def set_rate(self, rate: float) -> None:
        """Update the N/s readout."""
        self._rate = rate
        self.rate_label.setText('{:.0f}/s'.format(rate))

    def sample(self) -> None:
        """Push a sparkline sample, scaled to this row's recent peak."""
        self._hist.append(self._rate)
        peak = max(self._hist)
        self.spark.push(self._rate / peak if peak > 0 else 0.0)

    def pulse(self, phase: float) -> None:
        """Advance the dot: dim when silent, pulsing when messages flow."""
        self._opacity = 0.18 if self._rate < 0.5 else 0.45 + 0.55 * abs(math.sin(phase))
        self._dot.update()

    def _paint_dot(self, _e) -> None:
        p = QPainter(self._dot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('accent', self._opacity))
        p.drawEllipse(QRectF(0, 0, 5, 5))

    def paintEvent(self, _e) -> None:
        if self._zebra:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('row_alt'))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)


class OscStrip(QWidget):
    """The 14px pulsing strip the OSC column collapses to; click to open."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(STRIP_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('OSC activity')
        self._level = 0.0
        self._active = False

    def pulse(self, phase: float, active: bool) -> None:
        """Animate: brighter while messages flow."""
        self._active = active
        self._level = 0.35 + 0.65 * abs(math.sin(phase)) if active else 0.0
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked()

    def clicked(self) -> None:  # replaced by the pane
        return None

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), color('rail'))
        p.setPen(QPen(color('border'), 1))
        p.drawLine(0, 0, 0, self.height())
        bar = QRectF(self.width() / 2 - 1.5, 0, 3, 0)
        bar.setTop(self.height() * 0.5 - 24)
        bar.setBottom(self.height() * 0.5 + 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('accent', 0.2 + 0.7 * self._level) if self._active else color('border_2'))
        p.drawRoundedRect(bar, 1.5, 1.5)


class OscColumn(QWidget):
    """
    The 322px OSC activity column: header with total msgs/s, one row per
    address family, and the queue / dropped / uptime footer. At the minimum
    window width it also hosts the meters (`meter_slot`).
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName('oscColumn')
        self.setFixedWidth(COLUMN_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.rows: dict[str, ActivityRow] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 16, 16, 10)
        title = QLabel('OSC ACTIVITY')
        tf = Font.ui(12)
        tf.setLetterSpacing(tf.SpacingType.AbsoluteSpacing, 1.2)
        title.setFont(tf)
        title.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
        self.total_label = QLabel('0 msg/s')
        self.total_label.setFont(Font.ui(11))
        self.total_label.setStyleSheet(f"color: {color('accent').name()}; background: transparent;")
        hl.addWidget(title)
        hl.addStretch(1)
        hl.addWidget(self.total_label)
        lay.addWidget(head)

        self.meter_slot = QWidget()
        self.meter_grid = QGridLayout(self.meter_slot)
        self.meter_grid.setContentsMargins(10, 0, 10, 10)
        self.meter_grid.setSpacing(8)
        self.meter_slot.hide()
        lay.addWidget(self.meter_slot)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet('QScrollArea { background: transparent; } '
                                   'QScrollArea > QWidget > QWidget { background: transparent; }')
        self._body = QWidget()
        self._rows_lay = QVBoxLayout(self._body)
        self._rows_lay.setContentsMargins(10, 0, 10, 10)
        self._rows_lay.setSpacing(3)
        self._rows_lay.addStretch(1)
        self._scroll.setWidget(self._body)
        lay.addWidget(self._scroll, 1)

        foot = QWidget()
        foot.setObjectName('oscFooter')
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(16, 12, 16, 12)
        fl.setSpacing(7)
        self.queue_value = self._footer_row(fl, 'Queue')
        self.dropped_value = self._footer_row(fl, 'Dropped')
        self.uptime_value = self._footer_row(fl, 'Uptime')
        lay.addWidget(foot)
        self._foot = foot

    @staticmethod
    def _footer_row(parent_lay: QVBoxLayout, name: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(name)
        label.setFont(Font.ui(11))
        label.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
        value = QLabel('0')
        value.setFont(Font.ui(11))
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setStyleSheet(f"color: {color('text').name()}; background: transparent;")
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(value)
        parent_lay.addLayout(row)
        return value

    def set_families(self, families: list[str]) -> None:
        """Rebuild the rows for a new address list."""
        for row in self.rows.values():
            row.setParent(None)
            row.deleteLater()
        self.rows = {}
        for i, fam in enumerate(families):
            row = ActivityRow(fam, zebra=i % 2 == 0)
            self.rows[fam] = row
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)

    def add_family(self, family: str) -> None:
        """Append a row for an address first seen on the wire."""
        if family in self.rows:
            return
        row = ActivityRow(family, zebra=len(self.rows) % 2 == 0)
        self.rows[family] = row
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)

    def set_footer(self, queue: str, dropped: str, uptime: str, queue_warn: bool, dropped_warn: bool) -> None:
        """Update the footer readouts and their warn colouring."""
        self.queue_value.setText(queue)
        self.dropped_value.setText(dropped)
        self.uptime_value.setText(uptime)
        for label, warn in ((self.queue_value, queue_warn), (self.dropped_value, dropped_warn)):
            label.setStyleSheet(
                f"color: {color('warn' if warn else 'text').name()}; background: transparent;")

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), color('rail'))
        p.setPen(QPen(color('border'), 1))
        p.drawLine(0, 0, 0, self.height())
        y = self._foot.geometry().top()
        p.drawLine(0, y, self.width(), y)


# ============================================================================
# PANE
# ============================================================================
class LivePane(QWidget):
    """
    Stage, meters and OSC activity column, laid out for the window's current
    layout mode (see module docstring).

    Args:
        window: The MainWindow (config, engine, actions, layout_mode).
    """

    def __init__(self, window):
        super().__init__()
        self.setObjectName('livePane')
        self.window_ = window
        self.config = window.config
        self.engine = window.engine
        self._mode = getattr(window, 'layout_mode', 'comfortable')
        self._osc_open = self._mode == 'comfortable'
        self._phase = 0.0
        self._tick_n = 0
        self._running = False
        self._dropped_prev = 0
        self._drop_warn_until = -1.0
        self._visible_sent: bool | None = None
        self._last_status: dict = {}
        self.world_coords = False
        self.tracker = ActivityTracker()

        # Left column: stage over the meters
        self._left = QWidget(self)
        left = QVBoxLayout(self._left)
        left.setContentsMargins(PAD, PAD, PAD, PAD)
        left.setSpacing(14)
        self.stage = Stage()
        left.addWidget(self.stage, 1)
        self._meter_row = QWidget()
        self._meter_row_lay = QHBoxLayout(self._meter_row)
        self._meter_row_lay.setContentsMargins(0, 0, 0, 0)
        self._meter_row_lay.setSpacing(10)
        left.addWidget(self._meter_row)

        self.fps_meter = Meter('Frame rate', '0', 'fps', 0.0)
        self.points_meter = Meter('Landmarks sent', '0', 'pts/frame', 0.0)
        self.detections_meter = Meter('Detections', '0 pose · 0 hands', '', 0.0, value_px=17)
        self.latency_meter = Meter('Send latency', '0', 'ms', 0.0)
        self.meters = [self.fps_meter, self.points_meter, self.detections_meter, self.latency_meter]

        self.column = OscColumn(self)
        self.strip = OscStrip(self)
        self.strip.clicked = self.toggle_osc  # type: ignore[method-assign]

        self._place_meters(self._mode == 'minimum')
        self._rebuild_rows()
        self.refresh()
        self._clear_readouts()
        self._apply_open()

        self.stage.mirror_chip.toggled.connect(self._on_mirror)
        self.stage.world_chip.toggled.connect(self._on_world)
        self._left.installEventFilter(self)

        self._timer = QTimer(self)
        self._timer.setInterval(PULSE_MS)
        self._timer.timeout.connect(self.tick)

        e = self.engine
        e.statusReceived.connect(self.on_status)
        e.frameReady.connect(self.on_frame)
        e.stateChanged.connect(self.on_state)
        e.phaseChanged.connect(self.on_phase)
        window.layoutModeChanged.connect(self.set_layout_mode)
        window.paneChanged.connect(self._on_pane_changed)
        # create() runs before the window builds its actions
        QTimer.singleShot(0, self._wire_actions)

    # ------------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------------
    def _wire_actions(self) -> None:
        action = self.window_.actions.get('toggle_osc')
        if action is not None:
            action.triggered.connect(self.toggle_osc)

    def eventFilter(self, obj, e) -> bool:
        # A click on the stage dismisses the overlaid column
        if obj is self._left and e.type() == QEvent.Type.MouseButtonPress and self._overlaid():
            self.set_osc_open(False)
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self._overlaid():
            self.set_osc_open(False)
            return
        super().keyPressEvent(e)

    def _on_pane_changed(self, key: str) -> None:
        if key == 'live':
            self.refresh()

    # ------------------------------------------------------------------------
    # Layout modes
    # ------------------------------------------------------------------------
    def layout_mode(self) -> str:
        """The layout mode the pane is laid out for."""
        return self._mode

    def set_layout_mode(self, mode: str) -> None:
        """React to MainWindow.layoutModeChanged."""
        if mode == self._mode:
            return
        self._mode = mode
        self._osc_open = mode == 'comfortable'
        self._place_meters(mode == 'minimum')
        self._apply_open()

    def osc_open(self) -> bool:
        """True when the OSC column is open (docked or overlaid)."""
        return self._osc_open

    def _overlaid(self) -> bool:
        return self._osc_open and self._mode != 'comfortable'

    def toggle_osc(self) -> None:
        """Toggle the OSC activity column (the strip, or the View menu / Ctrl+Shift+O)."""
        self.set_osc_open(not self._osc_open)

    def set_osc_open(self, open_: bool) -> None:
        """Open or close the OSC column."""
        self._osc_open = bool(open_)
        self._apply_open()

    def _apply_open(self) -> None:
        self.column.setVisible(self._osc_open)
        self.strip.setVisible(not self._osc_open or self._overlaid())
        self._relayout()

    def _place_meters(self, minimum: bool) -> None:
        """Four across under the stage, or 2 x 2 in the OSC column at the minimum width."""
        for m in self.meters:
            m.setParent(None)
        if minimum:
            self._meter_row.hide()
            self.column.meter_slot.show()
            # Detections (17px, widest readout) and latency take a full row each
            grid = self.column.meter_grid
            fps, pts, det, lat = self.meters
            grid.addWidget(fps, 0, 0)
            grid.addWidget(pts, 0, 1)
            grid.addWidget(det, 1, 0, 1, 2)
            grid.addWidget(lat, 2, 0, 1, 2)
            for m in self.meters:
                m.setMinimumWidth(0)
                m.show()
        else:
            self.column.meter_slot.hide()
            self._meter_row.show()
            for m in self.meters:
                self._meter_row_lay.addWidget(m, 1)
                m.setMinimumWidth(130)
                m.show()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        docked = self._osc_open and self._mode == 'comfortable'
        if docked:
            self._left.setGeometry(0, 0, w - COLUMN_WIDTH, h)
            self.column.setGeometry(w - COLUMN_WIDTH, 0, COLUMN_WIDTH, h)
        else:
            self._left.setGeometry(0, 0, w - STRIP_WIDTH, h)
            self.strip.setGeometry(w - STRIP_WIDTH, 0, STRIP_WIDTH, h)
            self.column.setGeometry(w - STRIP_WIDTH - COLUMN_WIDTH + 1, 0, COLUMN_WIDTH, h)
        if self._osc_open and not docked:
            self.column.raise_()
        if not docked:
            self.strip.raise_()

    # ------------------------------------------------------------------------
    # Visibility / timers
    # ------------------------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._timer.start()
        self.refresh()
        self._send_visible(True)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()
        self._send_visible(False)

    def _send_visible(self, visible: bool) -> None:
        """Tell the engine whether the preview is being consumed (skips frame copies when not)."""
        if self._visible_sent == visible:
            return
        self._visible_sent = visible
        self.engine.send({'cmd': 'visible', 'value': visible})

    def tick(self) -> None:
        """Animate dots and strip; push sparkline samples at ~2 Hz."""
        self._phase += 0.55
        self._tick_n += 1
        sample = self._tick_n % SPARK_EVERY == 0
        for i, row in enumerate(self.column.rows.values()):
            row.pulse(self._phase + i * 0.7)
            if sample:
                row.sample()
        self.strip.pulse(self._phase, self._running and self.tracker.total_rate() > 0)

    # ------------------------------------------------------------------------
    # Config-derived content
    # ------------------------------------------------------------------------
    def holistic_active(self) -> bool:
        """True when the holistic model is in use (all mode, not disabled, one pose)."""
        return (_cfg(self.window_, 'ui', 'mode', 'all') == 'all'
                and not _cfg(self.window_, 'performance', 'no_holistic', False)
                and int(_cfg(self.window_, 'mediapipe', 'num_poses', 1) or 1) == 1)

    def source_text(self) -> str:
        """Top-left chip text: camera description or the NDI source name."""
        if _cfg(self.window_, 'camera', 'use_ndi', False):
            return str(_cfg(self.window_, 'camera', 'ndi_source', '') or '') or 'NDI'
        return 'Camera {} · {}×{} · {} fps'.format(
            _cfg(self.window_, 'camera', 'device_id', 0), _cfg(self.window_, 'camera', 'width', 640),
            _cfg(self.window_, 'camera', 'height', 480), _cfg(self.window_, 'camera', 'fps', 30))

    def refresh(self) -> None:
        """Re-read chips and toggle states from the config."""
        self.stage.set_source(self.source_text(), self.holistic_active())
        chip = self.stage.mirror_chip
        chip.blockSignals(True)
        chip.setChecked(bool(_cfg(self.window_, 'display', 'mirror_preview', False)))
        chip.blockSignals(False)
        self.stage.world_chip.blockSignals(True)
        self.stage.world_chip.setChecked(self.world_coords)
        self.stage.world_chip.blockSignals(False)
        self._show_stats_line()

    def _rebuild_rows(self) -> None:
        proto = _cfg(self.window_, 'osc', 'protocol', 'float')
        mode = _cfg(self.window_, 'ui', 'mode', 'all')
        families = predict_families(proto, mode)
        self.tracker.set_families(families)
        self.column.set_families(families)

    # ------------------------------------------------------------------------
    # Toggle chips
    # ------------------------------------------------------------------------
    def _on_mirror(self, on: bool) -> None:
        """
        Mirror flips the drawn frame only. The engine applies it to the frames
        it sends, so this writes display.mirror_preview and pushes it live
        (the next start picks it up when the engine is stopped).
        """
        self.config.set('display', 'mirror_preview', bool(on))
        self.config.save()
        self.engine.set_live('display.mirror_preview', bool(on))

    def _on_world(self, on: bool) -> None:
        """World coords is a display-only flag: the frame is video, nothing on the wire changes."""
        self.world_coords = bool(on)

    # ------------------------------------------------------------------------
    # Engine signals
    # ------------------------------------------------------------------------
    def on_frame(self, image: QImage) -> None:
        """Show a preview frame."""
        self.stage.set_frame(image)

    def on_phase(self, phase: str, _reason: str) -> None:
        """Phase changes clear the picture on faults and stop (the fault cards draw over it)."""
        if phase in ('stopped', 'no_camera', 'permission_denied', 'port_busy'):
            self.stage.set_frame(None)

    def on_state(self, state: str) -> None:
        """Reset the readouts on stop; rebuild the rows from the config on start."""
        if state == 'starting':
            self._rebuild_rows()
            self.refresh()
            self._visible_sent = None
            if self.isVisible():
                self._send_visible(True)
            else:
                self._send_visible(False)
        elif state == 'stopped':
            self._running = False
            self.tracker.reset()
            self._last_status = {}
            self._dropped_prev = 0
            self._drop_warn_until = -1.0
            self.stage.set_frame(None)
            self.stage.set_stats([])
            self._clear_readouts()
        if state in ('starting', 'running'):
            self._running = True

    def _clear_readouts(self) -> None:
        self.fps_meter.set_value('0', 0.0)
        self.points_meter.set_value('0', 0.0)
        self.detections_meter.set_value('0 pose · 0 hands', 0.0)
        self.latency_meter.set_value('0', 0.0)
        self.column.total_label.setText('0 msg/s')
        qs = self._queue_size()
        self.column.set_footer('0 / {}'.format(qs), '0', format_uptime(0), False, False)
        for row in self.column.rows.values():
            row.set_rate(0.0)

    def _queue_size(self) -> int:
        return int(_cfg(self.window_, 'osc', 'queue_size', 10) or 10)

    def on_status(self, msg: dict) -> None:
        """Update meters, rates and the footer from one status message."""
        self._last_status = msg
        self._running = True
        for fam in self.tracker.feed(msg):
            self.column.add_family(fam)
        rates = self.tracker.rates()
        for fam, row in self.column.rows.items():
            row.set_rate(rates.get(fam, 0.0))
        self.column.total_label.setText('{:.0f} msg/s'.format(self.tracker.total_rate()))

        fps = float(msg.get('fps') or 0.0)
        target = float(_cfg(self.window_, 'camera', 'fps', DEFAULT_FPS_TARGET) or DEFAULT_FPS_TARGET)
        self.fps_meter.set_value('{:.0f}'.format(fps), fps / max(target, fps, 1.0))

        det = msg.get('detections') or {}
        pose, hands = int(det.get('pose', 0) or 0), int(det.get('hands', 0) or 0)
        poses = max(1, int(_cfg(self.window_, 'mediapipe', 'num_poses', 1) or 1))
        pts = landmark_points(pose, hands)
        self.points_meter.set_value(str(pts), pts / float(landmark_points(poses, 2)))
        self.detections_meter.set_value(
            '{} {} · {} {}'.format(pose, 'pose' if pose == 1 else 'poses',
                                       hands, 'hand' if hands == 1 else 'hands'),
            (pose + hands) / float(poses + 2))
        latency = float(msg.get('latency_ms') or 0.0)
        self.latency_meter.set_value('{:.0f}'.format(latency), latency / 50.0)

        uptime = float(msg.get('uptime') or 0.0)
        queue = int(msg.get('queue') or 0)
        dropped = int(msg.get('dropped') or 0)
        if dropped > self._dropped_prev:
            self._drop_warn_until = uptime + DROP_WARN_SECONDS
        self._dropped_prev = dropped
        qs = self._queue_size()
        self.column.set_footer(
            '{} / {}'.format(queue, qs), str(dropped), format_uptime(uptime),
            queue_warn=queue > QUEUE_WARN * qs, dropped_warn=uptime < self._drop_warn_until)

        self._show_stats_line()

    def _show_stats_line(self) -> None:
        """The three-line stats overlay, shown while performance.show_fps is on."""
        msg = self._last_status
        if not _cfg(self.window_, 'performance', 'show_fps', False) or not msg:
            self.stage.set_stats([])
            return
        det = msg.get('detections') or {}
        self.stage.set_stats([
            'FPS {:.1f}'.format(float(msg.get('fps') or 0.0)),
            'POSE {} \u00b7 HANDS {}'.format(int(det.get('pose', 0) or 0), int(det.get('hands', 0) or 0)),
            'SEND {:.0f} ms'.format(float(msg.get('latency_ms') or 0.0))])


def create(window) -> QWidget:
    """
    Build the Live pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget (a `LivePane`).
    """
    return LivePane(window)
