#!/usr/bin/env python3
"""
Fault States Overlay

A card over the dimmed Live stage for every engine state other than running
(#67): starting, reconnecting, camera not found, camera access needed and
stopped. The card is chosen from the status channel's `phase` (#65) and the
controller's lifecycle state.

`port_busy` has no card: OSC output is an unbound UDP send, which cannot fail
on a busy port, so the engine never reports it (see src/runtime.py).

A camera fault (no_camera, permission_denied) ends the engine process, so the
`stopped` state follows it immediately. The fault card is kept until the next
start rather than being replaced by the generic Stopped card.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve, QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation,
)
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ..tokens import Font, color

# ============================================================================
# CONSTANTS
# ============================================================================
CARD_WIDTH = 404
CAMERA_SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Camera'

LOADING_PHASES = ('opening_camera', 'loading_model', 'warming_up')
STICKY_PHASES = ('no_camera', 'permission_denied')

#: Fault key -> Troubleshooting heading slug (headings added with the docs, #75)
HELP_SLUGS = {
    'loading': 'starting',
    'reconnecting': 'reconnecting',
    'no_camera': 'camera-not-found',
    'permission_denied': 'camera-access-needed',
    'stopped': 'stopped',
}


# ============================================================================
# CARD CONTENT
# ============================================================================
@dataclass
class Card:
    """What one fault card shows."""

    key: str
    glyph: str
    tone: str
    title: str
    body: str
    help_slug: str
    step: Optional[Tuple[str, float, str]] = None      # (label, fraction, "n / total")
    actions: List[Tuple[str, str]] = field(default_factory=list)   # (label, action id); first is primary


def holistic_active(config) -> bool:
    """True when the holistic model is in use (all mode, not disabled, one pose)."""
    return (config.get('ui', 'mode', 'all') == 'all'
            and not config.get('performance', 'no_holistic', False)
            and int(config.get('mediapipe', 'num_poses', 1) or 1) == 1)


def model_files(config) -> str:
    """The model file name(s) the engine loads for the current configuration."""
    mode = config.get('ui', 'mode', 'all')
    pose = 'pose_landmarker_{}.task'.format(config.get('mediapipe', 'pose_model_type', 'lite'))
    if holistic_active(config):
        return 'holistic_landmarker.task'
    if mode == 'pose':
        return pose
    if mode == 'hand':
        return 'hand_landmarker.task'
    return pose + ' + hand_landmarker.task'


def source_name(config) -> str:
    """'Camera 0' or the NDI source name."""
    if config.get('camera', 'use_ndi', False):
        return str(config.get('camera', 'ndi_source', '') or '') or 'the NDI source'
    return 'Camera {}'.format(config.get('camera', 'device_id', 0))


def build_card(key: str, config, phase: Optional[str] = None) -> Card:
    """
    The card for a fault key, interpolated with the live configuration.

    Args:
        key: 'loading', 'reconnecting', 'no_camera', 'permission_denied' or 'stopped'.
        config: The Config (camera / NDI input, models).
        phase: The startup phase for the loading card.

    Returns:
        The Card.
    """
    ndi = bool(config.get('camera', 'use_ndi', False))
    source = source_name(config)
    slug = HELP_SLUGS[key]
    if key == 'loading':
        phase = phase if phase in LOADING_PHASES else 'opening_camera'
        index = LOADING_PHASES.index(phase)
        label = {'opening_camera': 'Connecting to NDI source…' if ndi else 'Opening camera…',
                 'loading_model': model_files(config),
                 'warming_up': 'Warming up…'}[phase]
        fraction = (0.15, 0.55, 0.9)[index]
        return Card(key, '◐', 'accent', 'Starting',
                    'Loading the model and opening {}.'.format(source), slug,
                    step=(label, fraction, '{} / {}'.format(index + 1, len(LOADING_PHASES))))
    if key == 'reconnecting':
        return Card(key, '◐', 'warn', 'Reconnecting',
                    'Waiting for {} to return.'.format(source), slug, actions=[('Stop', 'stop')])
    if key == 'no_camera':
        if ndi:
            return Card(key, '!', 'warn', 'NDI source not found',
                        'Check the sender is running on your network and not already in use.', slug,
                        actions=[('Rescan sources', 'restart'), ('Use a camera instead', 'use_camera')])
        return Card(key, '!', 'warn', 'Camera not found',
                    'Check {} is connected and not in use by another app.'.format(source), slug,
                    actions=[('Rescan devices', 'restart'), ('Use NDI instead', 'use_ndi')])
    if key == 'permission_denied':
        return Card(key, '⦸', 'error', 'Camera access needed',
                    'Turn on Gesture in System Settings → Privacy & Security → Camera.', slug,
                    actions=[('Open System Settings', 'open_settings'), ('Recheck', 'restart')])
    return Card('stopped', '■', 'dim', 'Stopped',
                'Not tracking or sending. Settings changes apply when you start.', slug,
                actions=[('Start engine', 'start')])


# ============================================================================
# STATE
# ============================================================================
class FaultState:
    """
    Which card to show, from engine lifecycle state and status phases.
    Qt-free so the transitions are testable on their own.
    """

    def __init__(self, state: str = 'stopped'):
        self.state = state
        self.phase: Optional[str] = None
        self._sticky: Optional[str] = None
        self._key: Optional[str] = 'stopped' if state == 'stopped' else None

    @property
    def key(self) -> Optional[str]:
        """The card key, or None when no card should show."""
        return self._key

    def on_state(self, state: str) -> None:
        """Apply a controller lifecycle state (stopped, starting, running, stopping)."""
        self.state = state
        if state == 'starting':
            self.phase, self._sticky = None, None
            self._key = 'loading'
        elif state == 'running':
            if self._key in (None, 'stopped') or (self._key == 'loading' and self.phase is None):
                self._key = None
        elif state == 'stopped':
            self._key = self._sticky or 'stopped'

    def on_phase(self, phase: str) -> None:
        """Apply a status-channel phase."""
        if phase in LOADING_PHASES:
            self.phase = phase
            self._key = 'loading'
        elif phase == 'ready':
            self.phase = phase
            self._key = None
        elif phase == 'reconnecting':
            self._key = 'reconnecting'
        elif phase in STICKY_PHASES:
            self._sticky = self._key = phase
        elif phase == 'stopped':
            self._key = self._sticky or 'stopped'
        # port_busy is never emitted by the engine and has no card


# ============================================================================
# WIDGETS
# ============================================================================
class _ActionButton(QAbstractButton):
    """Outlined card action; the primary one takes the card's tone."""

    def __init__(self, text: str, tone: str, primary: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self._tone, self._primary, self._hover = tone, primary, False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFont(Font.ui(12.5, 500 if primary else 400))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 28, 32)

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        if self._hover or self.isDown():
            p.setBrush(QColor(255, 255, 255, 10))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        accent = color(self._tone if self._primary else 'border_2')
        if self.hasFocus():
            accent = color('accent')
        p.setPen(QPen(accent, 1))
        p.drawRoundedRect(r, 8, 8)
        p.setFont(self.font())
        p.setPen(color(self._tone if self._primary else 'dim'))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class _Link(QAbstractButton):
    """'Learn more' text link."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFont(Font.ui(11.5))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 4, 24)

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        f = self.font()
        f.setUnderline(self._hover or self.hasFocus())
        p.setFont(f)
        p.setPen(color('text_2' if self._hover else 'dim'))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


#: Design motion: the progress bar's `transition: width .4s cubic-bezier(.4,0,.2,1)`,
#: the scan sweep's `mp-sweep 4.5s linear infinite` (120px band) and the card fade
BAR_MS = 400
SWEEP_PERIOD_S = 4.5
SWEEP_BAND = 120
SWEEP_FRAME_MS = 33
FADE_IN_MS = 220
FADE_OUT_MS = 320


def _bezier_curve() -> QEasingCurve:
    """cubic-bezier(.4, 0, .2, 1), the design's standard easing."""
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.4, 0.0), QPointF(0.2, 1.0), QPointF(1.0, 1.0))
    return c


class _Bar(QWidget):
    """3px progress bar whose fill glides to a new fraction."""

    def __init__(self, fraction: float, parent: QWidget | None = None):
        super().__init__(parent)
        self._f = max(0.0, min(1.0, fraction))
        self.setFixedHeight(3)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(BAR_MS)
        self._anim.setEasingCurve(_bezier_curve())
        self._anim.valueChanged.connect(self._on_anim)

    def fraction(self) -> float:
        """The fraction currently drawn."""
        return self._f

    def set_fraction(self, fraction: float) -> None:
        """Glide the fill to `fraction` (0..1)."""
        self._anim.stop()
        self._anim.setStartValue(self._f)
        self._anim.setEndValue(max(0.0, min(1.0, fraction)))
        self._anim.start()

    def _on_anim(self, v) -> None:
        self._f = float(v)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('border'))
        p.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        p.setBrush(color('accent'))
        p.drawRoundedRect(QRectF(0, 0, self.width() * self._f, 3), 1.5, 1.5)


class _CardFrame(QWidget):
    """The card surface: gradient, tone-tinted border."""

    def __init__(self, tone: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._tone = tone
        self.setFixedWidth(CARD_WIDTH)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0, QColor('#20242C'))
        g.setColorAt(1, QColor('#171A20'))
        p.setBrush(g)
        p.setPen(QPen(color(self._tone, 0.4), 1))
        p.drawRoundedRect(r, 12, 12)


class FaultOverlay(QWidget):
    """
    Dim layer plus fault card, covering the Live stage. Hidden when there is
    no card. Emits no signals: actions go to `on_action(action_id)` (set by
    the controller) and the link to `on_help(slug)`.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('faultOverlay')
        self.card: Optional[Card] = None
        self.on_action = lambda _id: None
        self.on_help = lambda _slug: None
        self.buttons: dict[str, _ActionButton] = {}
        self.link: Optional[_Link] = None
        self._frame: Optional[_CardFrame] = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._step_label: Optional[QLabel] = None
        self._count_label: Optional[QLabel] = None
        self._body_label: Optional[QLabel] = None
        self._bar: Optional[_Bar] = None
        self._fade: Optional[QVariantAnimation] = None
        self._sweep_clock = QElapsedTimer()
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(SWEEP_FRAME_MS)
        self._sweep_timer.timeout.connect(self.update)
        self.hide()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.card is not None and self.card.key == 'loading' and not self._sweep_timer.isActive():
            self._sweep_clock.start()
            self._sweep_timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._sweep_timer.stop()  # nothing to animate off screen

    def sweeping(self) -> bool:
        """True while the scan sweep animates (the Starting card is up)."""
        return self._sweep_timer.isActive()

    def sweep_y(self) -> float:
        """Top of the sweep band right now (linear, wraps every 4.5 s)."""
        u = (self._sweep_clock.elapsed() / 1000.0 % SWEEP_PERIOD_S) / SWEEP_PERIOD_S
        return -SWEEP_BAND + u * (self.height() + SWEEP_BAND)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(11, 13, 17, 153))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        if self._sweep_timer.isActive():
            # mp-sweep: a soft accent band scanning down the stage while it starts
            y = self.sweep_y()
            g = QLinearGradient(0, y, 0, y + SWEEP_BAND)
            g.setColorAt(0, color('accent', 0.0))
            g.setColorAt(0.5, color('accent', 0.07))
            g.setColorAt(1, color('accent', 0.0))
            p.save()
            p.setClipRect(self.rect())
            p.fillRect(QRectF(0, y, self.width(), SWEEP_BAND), g)
            p.restore()

    def _fade_in(self) -> None:
        """Fade the overlay in (card appearing from nothing)."""
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        if self._fade is not None:
            self._fade.stop()
        fx = QGraphicsOpacityEffect(self)
        fx.setOpacity(0.0)
        self.setGraphicsEffect(fx)
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(FADE_IN_MS)
        anim.valueChanged.connect(lambda v: fx.setOpacity(float(v)))
        anim.finished.connect(lambda: self.setGraphicsEffect(None))
        self._fade = anim
        anim.start()

    def _fade_out_ghost(self) -> None:
        """Dissolve a still of the overlay over the stage as it hides (card dismissed)."""
        parent = self.parentWidget()
        if parent is None or not self.isVisible() or self.width() <= 0:
            return
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        ghost = QLabel(parent)
        ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ghost.setPixmap(self.grab())
        ghost.setGeometry(self.geometry())
        fx = QGraphicsOpacityEffect(ghost)
        ghost.setGraphicsEffect(fx)
        chip = getattr(parent, 'source_chip', None)
        if chip is not None:
            ghost.stackUnder(chip)
        ghost.show()
        anim = QVariantAnimation(ghost)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(FADE_OUT_MS)
        anim.valueChanged.connect(lambda v: fx.setOpacity(float(v)))
        anim.finished.connect(ghost.deleteLater)
        anim.start()

    def mousePressEvent(self, e) -> None:
        e.accept()

    def set_card(self, card: Optional[Card]) -> None:
        """Show `card`, or hide the overlay for None."""
        if (card is not None and card.key == 'loading' and self.card is not None
                and self.card.key == 'loading' and self._bar is not None and card.step is not None):
            # Next start-up step: the bar glides and the labels change in place
            label, fraction, count = card.step
            self._step_label.setText(label)
            self._count_label.setText(count)
            self._body_label.setText(card.body)
            self._bar.set_fraction(fraction)
            self.card = card
            return
        if card is None and self._frame is not None:
            self._fade_out_ghost()
        appearing = card is not None and not self.isVisible()
        self._sweep_timer.stop()
        self._bar = self._step_label = self._count_label = self._body_label = None
        if self._frame is not None:
            self._lay.removeWidget(self._frame)
            self._frame.setParent(None)
            self._frame.deleteLater()
            self._frame = None
        self.buttons, self.link, self.card = {}, None, card
        if card is None:
            self.hide()
            return
        frame = _CardFrame(card.tone)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(22, 22, 22, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(11)
        glyph = QLabel(card.glyph)
        glyph.setObjectName('faultGlyph')
        glyph.setFixedSize(30, 30)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setFont(Font.ui(15))
        glyph.setStyleSheet(
            f"color: {color(card.tone).name()}; background: transparent;"
            f" border: 1px solid {color(card.tone, 0.4).name(QColor.NameFormat.HexArgb)}; border-radius: 9px;")
        title = QLabel(card.title)
        title.setObjectName('faultTitle')
        title.setFont(Font.ui(14, 500))
        title.setStyleSheet(f"color: {color(card.tone).name()}; background: transparent;")
        head.addWidget(glyph)
        head.addWidget(title, 1)
        lay.addLayout(head)

        body = QLabel(card.body)
        body.setObjectName('faultBody')
        body.setWordWrap(True)
        body.setFont(Font.ui(12.5))
        body.setStyleSheet(f"color: {color('text_2').name()}; background: transparent;")
        lay.addWidget(body)
        self._body_label = body

        if card.step is not None:
            label, fraction, count = card.step
            row = QHBoxLayout()
            step = QLabel(label)
            step.setObjectName('faultStep')
            step.setFont(Font.ui(11))
            step.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
            n = QLabel(count)
            n.setFont(Font.ui(11))
            n.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
            row.addWidget(step, 1)
            row.addWidget(n)
            lay.addLayout(row)
            self._step_label, self._count_label = step, n
            self._bar = _Bar(fraction)
            lay.addWidget(self._bar)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        for i, (label, action_id) in enumerate(card.actions):
            b = _ActionButton(label, card.tone, primary=i == 0)
            b.clicked.connect(lambda _=False, a=action_id: self.on_action(a))
            self.buttons[action_id] = b
            foot.addWidget(b)
        foot.addStretch(1)
        self.link = _Link('Learn more')
        self.link.clicked.connect(lambda _=False, s=card.help_slug: self.on_help(s))
        foot.addWidget(self.link)
        lay.addLayout(foot)

        self._frame = frame
        self._lay.addWidget(frame, 0, Qt.AlignmentFlag.AlignCenter)
        self.show()
        self.raise_()
        if card.key == 'loading':
            self._sweep_clock.start()
            self._sweep_timer.start()
        if appearing:
            self._fade_in()


# ============================================================================
# CONTROLLER
# ============================================================================
class FaultController:
    """
    Connects the engine to the Live stage's fault overlay and performs the
    card actions.

    Args:
        window: The MainWindow (engine, config, show_pane).
        stage: The Live pane's Stage (hosts the overlay).
    """

    def __init__(self, window, stage):
        self.window = window
        self.engine = window.engine
        self.config = window.config
        self.state = FaultState(self.engine.state)
        self.overlay = FaultOverlay(stage)
        self.overlay.on_action = self.perform
        self.overlay.on_help = self.learn_more
        stage.set_overlay(self.overlay)
        self.engine.stateChanged.connect(self._on_state)
        self.engine.phaseChanged.connect(self._on_phase)
        self.render()

    def _on_state(self, state: str) -> None:
        self.state.on_state(state)
        self.render()

    def _on_phase(self, phase: str, _reason: str = '') -> None:
        self.state.on_phase(phase)
        self.render()

    def render(self) -> None:
        """Show the card for the current state."""
        key = self.state.key
        self.overlay.set_card(build_card(key, self.config, self.state.phase) if key else None)

    def refresh(self) -> None:
        """Re-render after a config change (camera/NDI names, model files)."""
        self.render()

    def learn_more(self, slug: str) -> None:
        """Open the Troubleshooting section for a card."""
        self.window.show_pane('help', 'troubleshooting#' + slug)

    def perform(self, action_id: str) -> None:
        """
        Run a card action.

        Args:
            action_id: stop, restart, start, use_ndi, use_camera or open_settings.
        """
        if action_id == 'stop':
            self.engine.stop()
        elif action_id == 'restart':
            self.engine.restart()
        elif action_id == 'start':
            self.engine.start()
        elif action_id == 'use_ndi':
            self.window.show_pane('input', 'camera.use_ndi')
        elif action_id == 'use_camera':
            self.window.show_pane('input', 'camera.use_ndi')
        elif action_id == 'open_settings':
            QDesktopServices.openUrl(QUrl(CAMERA_SETTINGS_URL))


# ============================================================================
# INSTALL
# ============================================================================
def install(window) -> None:
    """
    Register the faults overlay with the window: it lives on the Live pane's
    stage. MainWindow calls this once at construction.

    Args:
        window: The MainWindow.
    """
    pane = window.pane('live')
    stage = getattr(pane, 'stage', None)
    if stage is None or not hasattr(stage, 'set_overlay'):
        return
    window.faults = FaultController(window, stage)
    pane.faults = window.faults
    window.paneChanged.connect(lambda key: window.faults.refresh() if key == 'live' else None)
