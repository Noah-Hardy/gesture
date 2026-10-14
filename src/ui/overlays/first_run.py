#!/usr/bin/env python3
"""
first_run overlay

The first-run wizard (#69) and the one-time OSC output format notice (#83c).
Both are cards on `window.overlay_host()`. The wizard shows when
`ui.first_run_complete` is not set and can be reopened from Help -> Setup...;
the notice shows when the 0.3 -> 0.4 config migration left
`ui.protocol_notice_pending` set.

An upgrader (a config file that was migrated, hence pre-existing) only sees
the notice: the wizard is for people who have never configured Gesture.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ..tokens import Font, color
from ..widgets import Button

try:  # Qt >= 6.5
    from PySide6.QtCore import QCameraPermission
except ImportError:  # pragma: no cover
    QCameraPermission = None

# ============================================================================
# CONTENT
# ============================================================================
#: (name, host, port, note) -- from the design's RECEIVERS
RECEIVERS = [
    ('Isadora', '127.0.0.1', 1234, 'OSC Listener actor · default'),
    ('TouchDesigner', '127.0.0.1', 7000, 'OSC In DAT, default port'),
    ('Max / MSP', '127.0.0.1', 7400, 'udpreceive 7400'),
    ('Something else', '127.0.0.1', 1234, 'Editable in OSC Output'),
]

#: Step key -> (title, body, primary, secondary), from the design's FIRST_RUN
FIRST_RUN = {
    'welcome': ('Gesture streams a tracked body over OSC',
                'Camera or NDI in, pose and hand landmarks out.',
                'Get started', 'Skip setup'),
    'camera': ('Gesture needs your camera', 'Nothing is recorded.',
               'Allow camera access', 'Not now'),
    'receiver': ('Where should the landmarks go?', '', 'Continue', 'Skip'),
    'ready': ('Ready to track', '', 'Start tracking', 'Not yet'),
}

QUARANTINE_TITLE = 'This copy of Gesture is not notarized'
QUARANTINE_BODY = ('This build is signed ad hoc, so macOS quarantines it after download and may block '
                   'it or its updates. If that happens, run this in Terminal:')

NOTICE_TITLE = 'OSC output format changed'
NOTICE_BODY = ('Gesture 0.4.0 sends the float OSC format by default instead of the Legacy (0.2.x) '
               'format. Receivers built for 0.2 or 0.3 patches may need updating. '
               'You can switch back at any time in OSC Output.')

CARD_WIDTH = 560
CAMERA_SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Camera'


# ============================================================================
# PLATFORM HELPERS
# ============================================================================
def is_adhoc_signed() -> bool:
    """
    Whether the running app is a frozen, ad-hoc signed bundle (`codesign -dv`).

    Returns:
        True only for a packaged build whose signature is `adhoc`.
    """
    if not getattr(sys, 'frozen', False) or sys.platform != 'darwin':
        return False
    from ...updater import bundle_path
    app = bundle_path()
    if not app:
        return False
    try:
        out = subprocess.run(['/usr/bin/codesign', '-dv', app], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return 'Signature=adhoc' in (out.stderr + out.stdout)


def quarantine_command() -> str:
    """The documented quarantine-removal command for this install (docs/BUILDING.md)."""
    app = '/Applications/Gesture.app'
    if getattr(sys, 'frozen', False):
        from ...updater import bundle_path
        app = bundle_path() or app
    return f'xattr -dr com.apple.quarantine {app}'


def camera_status() -> str:
    """
    Current camera permission.

    Returns:
        'granted', 'denied' or 'undetermined'.
    """
    if QCameraPermission is None:
        return 'undetermined'
    status = QCoreApplication.instance().checkPermission(QCameraPermission())
    if status == Qt.PermissionStatus.Granted:
        return 'granted'
    if status == Qt.PermissionStatus.Denied:
        return 'denied'
    return 'undetermined'


def request_camera(context: QObject, callback) -> None:
    """
    Ask macOS for camera access (shows the system prompt the first time).

    Args:
        context: QObject that scopes the callback's lifetime.
        callback: Called with 'granted' or 'denied' once the user answered.
    """
    if QCameraPermission is None:
        callback('denied')
        return

    def done(_permission) -> None:
        callback(camera_status())

    QCoreApplication.instance().requestPermission(QCameraPermission(), context, done)


# ============================================================================
# SMALL WIDGETS
# ============================================================================
class _Clickable(QWidget):
    """Base for painted click targets."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class _Link(QLabel):
    """Text-only secondary action."""

    clicked = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFont(Font.ui(12.5))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._style(False)

    def _style(self, hot: bool) -> None:
        self.setStyleSheet(f"color: {color('text' if hot else 'dim').name()}; background: transparent;"
                           " padding: 6px 8px;")

    def enterEvent(self, _e) -> None:
        self._style(True)

    def leaveEvent(self, _e) -> None:
        self._style(False)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class _ReceiverCard(_Clickable):
    """One selectable receiver preset (name, address, note)."""

    def __init__(self, receiver: tuple, selected: bool, parent=None):
        super().__init__(parent)
        self._name, host, port, self._note = receiver
        self._addr = f'{host}:{port}'
        self._selected = selected
        self.setFixedHeight(84)
        self.setAccessibleName(self._name)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        edge = 'accent' if self._selected else ('dim' if self._hover else 'border_2')
        p.setBrush(color('field'))
        p.setPen(QPen(color(edge), 1))
        p.drawRoundedRect(r, 10, 10)
        x = 14
        if self._selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent'))
            p.drawEllipse(x, 19, 5, 5)
        p.setFont(Font.ui(13.5, 500))
        p.setPen(color('accent' if self._selected else 'text'))
        p.drawText(x + 13, 14, r.width(), 18, Qt.AlignmentFlag.AlignVCenter, self._name)
        p.setFont(Font.mono(11.5))
        p.setPen(color('dim'))
        p.drawText(x, 36, r.width(), 16, Qt.AlignmentFlag.AlignVCenter, self._addr)
        p.setFont(Font.ui(11))
        p.setPen(color('faint'))
        p.drawText(x, 56, r.width() - 2 * x, 16, Qt.AlignmentFlag.AlignVCenter, self._note)


class _Dots(QWidget):
    """Step indicator: the current step is a long dot, finished ones are dim accent."""

    def __init__(self, count: int, current: int, parent=None):
        super().__init__(parent)
        self._count, self._current = count, current
        self.setFixedSize(count * 11 + 12, 6)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        x = 0
        for n in range(self._count):
            w = 18 if n == self._current else 6
            p.setBrush(color('accent' if n == self._current else ('accent_2' if n < self._current else 'border_2')))
            p.drawRoundedRect(x, 0, w, 6, 3, 3)
            x += w + 5


def _label(text: str, font, fg: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setFont(font)
    lab.setWordWrap(wrap)
    # The app stylesheet overrides setFont() on labels, so the font goes in the QSS too
    lab.setStyleSheet(f"color: {color(fg).name()}; background: transparent; font-family: '{font.family()}';"
                      f" font-size: {font.pixelSize()}px; font-weight: {int(font.weight())};")
    return lab


def _box(parent_layout: QVBoxLayout) -> QHBoxLayout:
    """A bordered status strip appended to `parent_layout`; returns its row layout."""
    frame = QFrame()
    frame.setObjectName('frStrip')
    frame.setStyleSheet(f"QFrame#frStrip {{ background: {color('field').name()};"
                        f" border: 1px solid {color('border').name()}; border-radius: 10px; }}"
                        " QFrame#frStrip QLabel { border: none; }")
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 12, 14, 12)
    row.setSpacing(10)
    parent_layout.addWidget(frame)
    return row


def _dot(fg: str) -> QWidget:
    d = QWidget()
    d.setFixedSize(6, 6)
    d.setStyleSheet(f"background: {color(fg).name()}; border-radius: 3px; border: none;")
    return d


class _Card(QFrame):
    """Rounded panel the wizard and the notice sit in."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName('frCard')
        self.setFixedWidth(CARD_WIDTH)
        self.setStyleSheet(
            f"QFrame#frCard {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f" stop:0 {color('panel').name()}, stop:1 {color('panel_end').name()});"
            f" border: 1px solid {color('border_2').name()}; border-radius: 12px; }}")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(32, 30, 32, 26)
        self._lay.setSpacing(18)

    def clear(self) -> None:
        """Remove the current content."""
        self._drain(self._lay)

    @classmethod
    def _drain(cls, layout) -> None:
        """Delete every widget under `layout`, including those in nested layouts."""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                cls._drain(item.layout())

    def place(self) -> None:
        """Centre in the parent."""
        p = self.parentWidget()
        for child in self.findChildren(QWidget):
            child.show()  # freshly added children of a visible card are shown lazily; layouts skip hidden ones
        self._lay.invalidate()
        self._lay.activate()
        hfw = self._lay.heightForWidth(CARD_WIDTH) if self._lay.hasHeightForWidth() else -1
        self.resize(CARD_WIDTH, hfw if hfw > 0 else self._lay.sizeHint().height())
        if p is not None:
            self.move((p.width() - self.width()) // 2, max(16, (p.height() - self.height()) // 2))


# ============================================================================
# CONTROLLER
# ============================================================================
class FirstRun(QObject):
    """
    Owns the wizard card and the protocol notice card for one MainWindow.

    Args:
        window: The MainWindow.
    """

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        host = window.overlay_host()
        self.wizard = _Card(host)
        self.notice = _Card(host)
        host.installEventFilter(self)
        self._steps: list[str] = []
        self._step = 0
        self._preset = RECEIVERS[0][0]
        self._camera = 'undetermined'
        self._adhoc = None

    # --------------------------------------------------------------- lifecycle
    def eventFilter(self, obj, e) -> bool:
        if e.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            for card in (self.wizard, self.notice):
                if not card.isHidden():
                    card.place()
        return False

    def start(self) -> None:
        """Show whatever applies at launch: the wizard, else the pending notice."""
        cfg = self.window.config
        notice = bool(cfg.get('ui', 'protocol_notice_pending', False))
        complete = bool(cfg.get('ui', 'first_run_complete', False))
        if notice and not complete:
            # Only a migrated (pre-existing) config carries the flag: an upgrader
            cfg.set('ui', 'first_run_complete', True)
            complete = True
        if not complete:
            self.open_wizard()
        elif notice:
            self.open_notice()

    def open_wizard(self) -> None:
        """Show the wizard from step 1 (also Help -> Setup...)."""
        if self._adhoc is None:
            self._adhoc = is_adhoc_signed()
        self._steps = ['welcome'] + (['quarantine'] if self._adhoc else []) + ['camera', 'receiver', 'ready']
        self._step = 0
        self._preset = self._current_preset()
        self._camera = camera_status()
        self.notice.hide()
        self._render()
        self.wizard.show()
        self.wizard.raise_()
        self.wizard.place()
        self.wizard.setFocus()

    def _current_preset(self) -> str:
        cfg = self.window.config
        host, port = cfg.get('osc', 'host', ''), cfg.get('osc', 'port', 0)
        for name, h, p, _ in RECEIVERS[:-1]:
            if h == host and p == port:
                return name
        return RECEIVERS[0][0]

    def _receiver(self) -> tuple:
        return next(r for r in RECEIVERS if r[0] == self._preset)

    # ------------------------------------------------------------------ wizard
    def _go(self, step: int) -> None:
        self._step = step
        self._render()
        self.wizard.place()

    def _render(self) -> None:
        card = self.wizard
        card.clear()
        lay = card._lay
        key = self._steps[self._step]
        title, body, primary, secondary = FIRST_RUN.get(key, ('', '', 'Continue', 'Skip setup'))
        extra = []
        on_primary = lambda: self._go(self._step + 1)  # noqa: E731
        on_secondary = self._skip

        kicker = _label(f'Setup · step {self._step + 1} of {len(self._steps)}'.upper(),
                        Font.ui(11), 'accent')
        lay.addWidget(kicker)

        if key == 'quarantine':
            title, body, primary, secondary = QUARANTINE_TITLE, QUARANTINE_BODY, 'Continue', 'Skip setup'
        lay.addWidget(_label(title, Font.ui(25, 500), 'text'))
        if body:
            lay.addWidget(_label(body, Font.ui(14), 'dim'))

        if key == 'quarantine':
            row = _box(lay)
            code = _label(quarantine_command(), Font.mono(12), 'accent')
            code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(code, 1)
            copy = Button('Copy command')
            copy.clicked.connect(self._copy_quarantine)
            row.addWidget(copy)

        elif key == 'camera':
            state = self._camera
            text = {'granted': 'Camera access granted',
                    'denied': 'Camera access denied. Allow Gesture in System Settings > Privacy & Security > Camera.',
                    }.get(state, 'Camera access not requested yet')
            fg = {'granted': 'accent', 'denied': 'warn'}.get(state, 'dim')
            row = _box(lay)
            row.addWidget(_dot(fg))
            row.addWidget(_label(text, Font.ui(13), fg), 1)
            if state == 'granted':
                primary, secondary = 'Continue', ''
            elif state == 'denied':
                primary = 'Open System Settings'
                on_primary = lambda: QDesktopServices.openUrl(QUrl(CAMERA_SETTINGS_URL))  # noqa: E731
                secondary = 'Continue without camera'
                on_secondary = lambda: self._go(self._step + 1)  # noqa: E731
            else:
                on_primary = self._ask_camera

        elif key == 'receiver':
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            for i, rc in enumerate(RECEIVERS):
                c = _ReceiverCard(rc, rc[0] == self._preset)
                c.clicked.connect(lambda name=rc[0]: self._pick(name))
                grid.addWidget(c, i // 2, i % 2)
            lay.addLayout(grid)

        elif key == 'ready':
            rc = self._receiver()
            row = _box(lay)
            row.addWidget(_dot('accent'))
            row.addWidget(_label('SENDING TO', Font.ui(11), 'faint', wrap=False))
            row.addWidget(_label(rc[0], Font.ui(13), 'text', wrap=False))
            row.addWidget(_label(f'{rc[1]}:{rc[2]}', Font.mono(12.5), 'accent', wrap=False))
            row.addStretch(1)
            change = _Link('Change')
            change.clicked.connect(lambda: self._go(self._steps.index('receiver')))
            row.addWidget(change)
            on_primary = lambda: self._finish(start=True)  # noqa: E731
            on_secondary = lambda: self._finish(start=False)  # noqa: E731

        lay.addLayout(self._buttons(primary, on_primary, secondary, on_secondary, extra))

    def _buttons(self, primary, on_primary, secondary, on_secondary, extra) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 4, 0, 0)
        b = Button(primary, primary=True)
        b.setObjectName('frPrimary')
        b.clicked.connect(lambda: on_primary())
        row.addWidget(b)
        if secondary:
            s = _Link(secondary)
            s.setObjectName('frSecondary')
            s.clicked.connect(lambda: on_secondary())
            row.addWidget(s)
        row.addStretch(1)
        row.addWidget(_Dots(len(self._steps), self._step))
        return row

    def _pick(self, name: str) -> None:
        self._preset = name
        self._render()

    def _copy_quarantine(self) -> None:
        QGuiApplication.clipboard().setText(quarantine_command())
        self.window.toast('Command copied')

    def _ask_camera(self) -> None:
        def answered(state: str) -> None:
            self._camera = state
            if self._steps[self._step] == 'camera' and not self.wizard.isHidden():
                self._render()
                self.wizard.place()
        request_camera(self, answered)

    def _skip(self) -> None:
        """Close without applying anything; counts as done so it does not return."""
        self._close_wizard()

    def _close_wizard(self) -> None:
        cfg = self.window.config
        cfg.set('ui', 'first_run_complete', True)
        cfg.save()
        self.wizard.hide()
        if cfg.get('ui', 'protocol_notice_pending', False):
            self.open_notice()

    def _finish(self, start: bool) -> None:
        """
        Write the chosen receiver (one save) and optionally start tracking.

        Args:
            start: True for "Start tracking", False for "Not yet" (settings are
                still kept).
        """
        cfg = self.window.config
        name, host, port, _ = self._receiver()
        cfg.set('osc', 'host', host)
        cfg.set('osc', 'port', port)
        if name == 'Isadora':
            cfg.set('osc', 'protocol', 'float')  # Isadora cannot read JSON
        cfg.set('ui', 'first_run_complete', True)
        cfg.save()
        refresh = getattr(self.window, 'refresh_readouts', None)
        if refresh:
            refresh()
        self.wizard.hide()
        if start and not self.window.engine.is_running():
            self.window.engine.start()
        self.window.toast(f'Sending to {host}:{port}', 3000)
        if cfg.get('ui', 'protocol_notice_pending', False):
            self.open_notice()

    # ------------------------------------------------------------------ notice
    def open_notice(self) -> None:
        """Show the one-time OSC output format notice."""
        card = self.notice
        card.clear()
        lay = card._lay
        lay.addWidget(_label('NOTICE', Font.ui(11), 'warn'))
        lay.addWidget(_label(NOTICE_TITLE, Font.ui(25, 500), 'text'))
        lay.addWidget(_label(NOTICE_BODY, Font.ui(14), 'dim'))
        learn = _Link('Learn more')
        learn.setObjectName('frLearn')
        learn.clicked.connect(lambda: self.window.show_pane('help', 'osc-output'))
        lay.addWidget(learn, 0, Qt.AlignmentFlag.AlignLeft)
        row = QHBoxLayout()
        row.setSpacing(10)
        keep = Button('Keep Legacy (0.2.x) format')
        keep.setObjectName('frKeepLegacy')
        keep.clicked.connect(lambda: self._resolve_notice('legacy'))
        use = Button('Use float', primary=True)
        use.setObjectName('frUseFloat')
        use.clicked.connect(lambda: self._resolve_notice('float'))
        row.addStretch(1)
        row.addWidget(keep)
        row.addWidget(use)
        lay.addLayout(row)
        card.show()
        card.raise_()
        card.place()
        card.setFocus()

    def _resolve_notice(self, protocol: str) -> None:
        cfg = self.window.config
        cfg.set('osc', 'protocol', protocol)
        cfg.set('ui', 'protocol_notice_pending', False)
        cfg.save()
        self.notice.hide()
        refresh = getattr(self.window, 'refresh_readouts', None)
        if refresh:
            refresh()


# ============================================================================
# INSTALL
# ============================================================================
def install(window) -> None:
    """
    Register the first_run overlay with the window.

    Shows the wizard (or, for an upgrader, the protocol notice) when it
    applies, and wires Help -> Setup... to reopen the wizard.

    Args:
        window: The MainWindow.
    """
    ctrl = FirstRun(window)
    window.first_run = ctrl
    action = window.actions.get('setup')
    if action is not None:
        action.triggered.connect(lambda *_: ctrl.open_wizard())
    ctrl.start()
