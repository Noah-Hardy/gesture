#!/usr/bin/env python3
"""
Update overlay

The Qt replacement for src.update_dialog and LauncherGui._handle_update. The
network and filesystem work stays in src.updater.UpdateController on its worker
threads; this module only consumes the payloads it emits:

    none | available | error            (from a check)
    progress | verifying | ready | failed (from an install)

Payloads reach the Qt thread through a signal (the controller only ever calls
`put((tag, payload))` on its output "queue"), so no widget is touched from a
worker thread.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import webbrowser

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QMessageBox, QTextBrowser,
    QVBoxLayout, QWidget,
)

from src import docs
from src.ui.tokens import Font, color
from src.ui.widgets import Button
from src.updater import UpdateController, cleanup_stale, current_version, spawn_installer

TAG_UPDATE = 'update'
LAUNCH_CHECK_DELAY_MS = 1500   # deferred so a slow DNS lookup never delays first paint
CARD_WIDTH = 460
NOTES_MAX_HEIGHT = 220

STATUS_VERIFYING = 'Verifying checksum & signature'
STATUS_READY = 'Verified & ready to install'


def format_size(num_bytes: int) -> str:
    """
    Human size for the subtitle.

    Args:
        num_bytes: Size in bytes (0 or less means unknown).

    Returns:
        Text such as '41.2 MB', or '' when unknown.
    """
    if not num_bytes or num_bytes <= 0:
        return ''
    return f'{num_bytes / (1 << 20):.1f} MB'


# ============================================================================
# THREAD BRIDGE
# ============================================================================
class _Bridge(QObject):
    """Quacks like the queue.Queue UpdateController writes to; re-emits on the Qt thread."""

    payload = Signal(dict)

    def put(self, item) -> None:
        """Called from updater worker threads; the queued signal hops to the Qt thread."""
        _tag, data = item
        self.payload.emit(data)


# ============================================================================
# PROGRESS BAR
# ============================================================================
class _Bar(QWidget):
    """4px determinate trough with an accent fill."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fraction = 0.0
        self.setFixedHeight(4)

    def set_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self.update()

    def fraction(self) -> float:
        return self._fraction

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('border'))
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)
        if self._fraction > 0:
            p.setBrush(color('accent'))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self._fraction, self.height()), 2, 2)


class _FlatButton(QLabel):
    """Borderless text button (the design's 'Skip this version')."""

    clicked = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFont(Font.ui(12))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._set_tone('dim')

    def _set_tone(self, tone: str) -> None:
        self.setStyleSheet(
            f"color: {color(tone).name()}; padding: 8px 10px; background: transparent;")

    def enterEvent(self, _e) -> None:
        self._set_tone('text')

    def leaveEvent(self, _e) -> None:
        self._set_tone('dim')

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            e.accept()
            return
        super().keyPressEvent(e)


# ============================================================================
# CARD
# ============================================================================
class UpdateCard(QFrame):
    """
    The update dialog: title, subtitle, release notes, progress row, buttons.

    Signals:
        installRequested(), skipRequested(), laterRequested(), cancelRequested(),
        releasePageRequested()
    """

    installRequested = Signal()
    skipRequested = Signal()
    laterRequested = Signal()
    cancelRequested = Signal()
    releasePageRequested = Signal()

    def __init__(self, release, current: str, parent=None):
        super().__init__(parent)
        self.release = release
        self.setObjectName('updateCard')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            "QFrame#updateCard { border: 1px solid %s; border-radius: 12px;"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #232830, stop:1 #1B1E25); }"
            " QFrame#updateCard QLabel { background: transparent; border: none; }"
            % color('border_2').name())
        self._busy = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        inner = CARD_WIDTH - 48

        # Header: arrow badge + title + subtitle
        head = QHBoxLayout()
        head.setSpacing(12)
        badge = QLabel('↑')
        badge.setFixedSize(38, 38)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFont(Font.ui(17))
        badge.setStyleSheet(
            "color: %s; border: 1px solid %s; border-radius: 10px; background: transparent;"
            % (color('accent').name(), color('accent_2').name()))
        head.addWidget(badge)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = QLabel(f'Gesture {release.version} is available')
        self.title_label.setFont(Font.ui(15, 500))
        self.title_label.setStyleSheet(f"color: {color('text').name()};")
        size = format_size(getattr(release, 'zip_size', 0))
        you = f'You have {current}' if current else 'You have an older version'
        self.subtitle_label = QLabel(f'{you} · {size}' if size else you)
        self.subtitle_label.setFont(Font.ui(12))
        self.subtitle_label.setStyleSheet(f"color: {color('dim').name()};")
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)
        lay.addLayout(head)

        # Release notes
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setFrameShape(QFrame.Shape.NoFrame)
        self.notes.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.notes.setStyleSheet(
            "QTextBrowser { background: transparent; border: none;"
            " border-left: 1px solid %s; padding-left: 8px; }" % color('border_2').name())
        body = (release.notes or '').strip() or 'No release notes were provided.'
        self.notes.setDocument(docs.render_qt(docs.parse(body), parent=self.notes))
        self.notes.document().setDocumentMargin(4)
        self.notes.setFixedWidth(inner)
        self.notes.document().setTextWidth(inner - 14)
        h = int(self.notes.document().size().height()) + 8
        self.notes.setFixedHeight(max(48, min(NOTES_MAX_HEIGHT, h)))
        lay.addWidget(self.notes)

        # Progress row
        self.progress_box = QWidget()
        self.progress_box.setFixedWidth(inner)
        pv = QVBoxLayout(self.progress_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(7)
        row = QHBoxLayout()
        self.status_label = QLabel('')
        self.status_label.setFont(Font.ui(11))
        self.status_label.setWordWrap(True)
        self._status_tone('dim')
        self.percent_label = QLabel('')
        self.percent_label.setFont(Font.ui(11))
        self.percent_label.setStyleSheet(f"color: {color('dim').name()};")
        row.addWidget(self.status_label, 1)
        row.addWidget(self.percent_label, 0, Qt.AlignmentFlag.AlignTop)
        pv.addLayout(row)
        self.bar = _Bar()
        pv.addWidget(self.bar)
        lay.addWidget(self.progress_box)
        self.progress_box.hide()

        # Buttons
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        lay.addLayout(self.buttons)
        self._button_widgets: list[QWidget] = []

        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc.activated.connect(self._escape)

        self._show_notes_buttons()

    def _status_tone(self, tone: str) -> None:
        self.status_label.setStyleSheet(f"color: {color(tone).name()};")

    # ------------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------------
    def _clear_buttons(self) -> None:
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._button_widgets = []

    def _add(self, text: str, signal, *, primary: bool = False, flat: bool = False):
        b = _FlatButton(text) if flat else Button(text, primary=primary)
        b.clicked.connect(lambda *_: signal.emit())
        self.buttons.addWidget(b)
        self._button_widgets.append(b)
        return b

    def _show_notes_buttons(self) -> None:
        self._busy = False
        self._clear_buttons()
        self.skip_button = self._add('Skip this version', self.skipRequested, flat=True)
        self.buttons.addStretch(1)
        self.later_button = self._add('Later', self.laterRequested)
        self.install_button = self._add('Install & relaunch', self.installRequested, primary=True)

    def _show_cancel_button(self) -> None:
        self._clear_buttons()
        self.buttons.addStretch(1)
        self.cancel_button = self._add('Cancel', self.cancelRequested)

    def button_texts(self) -> list[str]:
        """Texts of the current buttons, left to right."""
        return [b.text() for b in self._button_widgets]

    def _escape(self) -> None:
        if not self._busy:
            self.laterRequested.emit()

    @property
    def busy(self) -> bool:
        """True while a download/verify is in flight (the card must not be dismissed)."""
        return self._busy

    # ------------------------------------------------------------------------
    # States driven by UpdateController payloads
    # ------------------------------------------------------------------------
    def _enter_busy(self) -> None:
        if not self._busy:
            self._busy = True
            self.progress_box.show()
            self._show_cancel_button()
        self.bar.show()
        self._status_tone('dim')

    def show_progress(self, done: int, total: int) -> None:
        """Downloading: `done` of `total` bytes."""
        self._enter_busy()
        self.status_label.setText('Downloading')
        if total:
            self.bar.set_fraction(done / total)
            self.percent_label.setText(f'{min(100, int(done * 100 / total))}%')
        else:
            self.bar.set_fraction(0)
            self.percent_label.setText('')

    def show_verifying(self, phase: str = '') -> None:
        """Unpacking / checking the checksum and signature."""
        self._enter_busy()
        self.bar.set_fraction(1.0)
        self.status_label.setText(STATUS_VERIFYING)
        self.percent_label.setText('')

    def show_ready(self) -> None:
        """Staged and verified; nothing left to click (the relaunch follows)."""
        self._enter_busy()
        self.bar.set_fraction(1.0)
        self.status_label.setText(STATUS_READY)
        self.percent_label.setText('')
        self._clear_buttons()

    def show_failed(self, message: str) -> None:
        """Terminal failure: show the reason and offer the release page."""
        self._busy = False
        self.progress_box.show()
        self.title_label.setText('Update failed')
        self.notes.hide()
        self.bar.hide()
        self.percent_label.setText('')
        self.status_label.setText(message or 'The update could not be installed.')
        self.status_label.setFixedHeight(self.status_label.heightForWidth(CARD_WIDTH - 48 - 50))
        self._status_tone('error')
        self._clear_buttons()
        self.buttons.addStretch(1)
        self.close_button = self._add('Close', self.laterRequested)
        self.page_button = self._add('Open release page', self.releasePageRequested, primary=True)


# ============================================================================
# CONTROLLER
# ============================================================================
class UpdateOverlay(QObject):
    """
    Glue between the window, src.updater.UpdateController and the UpdateCard.

    Args:
        window: The MainWindow.
        controller_factory: `(queue_like, config, tag) -> UpdateController`; tests inject a fake.
    """

    def __init__(self, window, controller_factory=UpdateController):
        super().__init__(window)
        self.window = window
        self.config = window.config
        self.release = None
        self.card: UpdateCard | None = None
        self._bridge = _Bridge(self)
        self._bridge.payload.connect(self.handle_payload)
        self.updater = controller_factory(self._bridge, self.config, TAG_UPDATE)

    # ------------------------------------------------------------------------
    # Checking
    # ------------------------------------------------------------------------
    def check(self, manual: bool = False) -> None:
        """Help > Check for Updates, and the silent launch check."""
        if self.updater.busy:
            if manual:
                self.window.toast('Already checking for updates...')
            return
        if manual:
            self.window.toast('Checking for updates...')
        self.updater.check_async(manual=manual)

    def launch_check(self) -> None:
        """Silent check at launch; the updater itself honours check_on_launch and rate limits."""
        try:
            cleanup_stale()
        except Exception:
            pass
        if self.config.get('updates', 'check_on_launch', True):
            self.check(manual=False)

    # ------------------------------------------------------------------------
    # Payloads (Qt thread only)
    # ------------------------------------------------------------------------
    def handle_payload(self, payload: dict) -> None:
        """Dispatch one UpdateController payload."""
        persist = payload.get('persist')
        if persist:
            for key, value in persist.items():
                self.config.set('updates', key, value)
            self.config.save()

        kind = payload.get('kind')
        if kind == 'none':
            if payload.get('manual'):
                self.window.toast(payload.get('message') or "You're up to date")
            return
        if kind == 'error':
            if payload.get('manual'):
                self.window.toast(payload.get('message') or "Couldn't check for updates.", 6000)
            return
        if kind == 'available':
            self.release = payload.get('release')
            self.window.set_update_available(self.release)
            self.show_dialog()
            return

        # Everything else is an install-flow update for the open card
        card = self.card
        if card is None:
            return
        if kind == 'progress':
            card.show_progress(payload.get('done', 0), payload.get('total', 0))
        elif kind == 'verifying':
            card.show_verifying(payload.get('phase', ''))
        elif kind == 'ready':
            card.show_ready()
            self._install_ready(payload.get('staged'))
        elif kind == 'failed':
            card.show_failed(payload.get('message', ''))
        self._place(card)

    # ------------------------------------------------------------------------
    # Card lifecycle
    # ------------------------------------------------------------------------
    def show_dialog(self) -> None:
        """Open the card for the pending release (raise it if already open)."""
        if self.release is None:
            return
        if self.card is not None:
            self.card.show()
            self.card.raise_()
            return
        host = self.window.overlay_host()
        card = UpdateCard(self.release, current_version(), host)
        card.installRequested.connect(self._start_install)
        card.skipRequested.connect(self._skip)
        card.laterRequested.connect(self._later)
        card.cancelRequested.connect(self.updater.cancel)
        card.releasePageRequested.connect(self._open_release_page)
        self.card = card
        host.installEventFilter(self)
        self._place(card)
        card.show()
        card.raise_()
        card.setFocus()

    def eventFilter(self, obj, e) -> bool:
        if self.card is not None and e.type() == QEvent.Type.Resize:
            self._place(self.card)
        return False

    def _place(self, card: UpdateCard) -> None:
        host = self.window.overlay_host()
        card.adjustSize()
        card.move(max(0, (host.width() - card.width()) // 2),
                  max(0, (host.height() - card.height()) // 2))

    def _forget(self) -> None:
        card, self.card = self.card, None
        if card is not None:
            card.hide()
            card.setParent(None)
            card.deleteLater()

    def _later(self) -> None:
        if self.card is not None and self.card.busy:
            return  # don't close out from under a running install
        self._forget()

    def _skip(self) -> None:
        if self.release is not None:
            self.config.set('updates', 'skipped_version', self.release.tag)
            self.config.save()
        self.release = None
        self.window.set_update_available(None)
        self._forget()

    def _open_release_page(self) -> None:
        url = getattr(self.release, 'html_url', '') or ''
        if url.startswith(('http://', 'https://')):
            try:
                webbrowser.open(url)
            except Exception:
                pass

    # ------------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------------
    def _confirm_stop_engine(self) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, 'Stop engine and install?',
                          'Gesture needs to stop the tracking engine to install this update. '
                          'Stop it now?',
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                          self.window)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _start_install(self) -> None:
        if self.window.engine.is_running() and not self._confirm_stop_engine():
            return
        self.updater.install_async(self.release)

    def _install_ready(self, staged) -> None:
        """
        Update verified and staged: stop the engine, hand off to the installer, quit.

        The swap script waits for this process to exit before moving the new
        bundle into place, so spawning it must be the last thing we do.
        """
        QTimer.singleShot(0, lambda: self._finish_install(staged))

    def _finish_install(self, staged) -> None:
        try:
            self.config.save()
        except Exception:
            pass
        self.window.engine.stop_and_wait()
        try:
            spawn_installer(staged)
        except Exception as e:
            self.window.engine.output.emit('Failed to launch the installer: {}'.format(e))
            if self.card is not None:
                self.card.show_failed("Couldn't launch the installer: {}".format(e))
                self._place(self.card)
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()


# ============================================================================
# INSTALL
# ============================================================================
def install(window, controller_factory=UpdateController) -> UpdateOverlay:
    """
    Wire the Check for Updates action, the rail's update card and the launch check.

    Args:
        window: The MainWindow.
        controller_factory: Override for tests.

    Returns:
        The UpdateOverlay (also stored as `window.update_overlay`).
    """
    overlay = UpdateOverlay(window, controller_factory)
    window.update_overlay = overlay
    action = window.actions.get('check_updates')
    if action is not None:
        action.triggered.connect(lambda *_: overlay.check(manual=True))
    window.updateCardClicked.connect(overlay.show_dialog)
    QTimer.singleShot(LAUNCH_CHECK_DELAY_MS, overlay.launch_check)
    return overlay
