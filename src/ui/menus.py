#!/usr/bin/env python3
"""
Menus and Status Item

The native menu bar (Gesture, File, Engine, View, Window, Help), the status-bar
item (QSystemTrayIcon, which is an NSStatusItem on macOS) so the engine can run
with the window closed, and the keyboard focus order of the shell.

MainWindow calls `install()` once at construction; the QActions it builds
(with their shortcuts) live in `window.actions`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QAction, QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from src.icon_geometry import ACCENT, DIM, TIERS, scaled_point, tier_for

STATUS_ICON_SIZE = 18


# ============================================================================
# STATUS ICON
# ============================================================================
def status_pixmap(streaming: bool, size: int = STATUS_ICON_SIZE, dpr: float = 2.0) -> QPixmap:
    """
    Draw the icon mark, without its tile, in one colour for the status bar.

    Args:
        streaming: True for the accent colour, False for the dim colour.
        size: Logical edge length in pixels.
        dpr: Device pixel ratio of the pixmap.

    Returns:
        A transparent pixmap with the bones and nodes in a single colour.
    """
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    tier = TIERS[tier_for(size)]
    colour = QColor(ACCENT if streaming else DIM)
    unit = size / 100.0

    def pt(point) -> QPointF:
        x, y = scaled_point(point, tier.scale)
        return QPointF(x * unit, y * unit)

    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # A heavier stroke than the app icon keeps the figure legible at 18px
    pen = QPen(colour, max(1.5, tier.weight * tier.scale * unit * 1.4))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    for chain in tier.bones:
        path = QPainterPath(pt(chain[0]))
        for point in chain[1:]:
            path.lineTo(pt(point))
        p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    for point, radius in tier.nodes:
        r = radius * tier.scale * unit * 1.2
        c = pt(point)
        p.drawEllipse(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
    p.end()
    return px


def status_icon(streaming: bool) -> QIcon:
    """The status-bar icon for the engine state (accent streaming, dim stopped)."""
    return QIcon(status_pixmap(streaming))


# ============================================================================
# STATUS ITEM
# ============================================================================
class StatusItem:
    """
    The status-bar item. Menu: Start/Stop, destination, fps, Open Gesture, Quit.

    Showing it makes closing the window hide it instead of quitting, so the
    engine keeps running; Quit (here, in the menu bar or Cmd+Q) stops the
    engine and exits.
    """

    def __init__(self, window) -> None:
        """
        Args:
            window: The MainWindow.
        """
        self.window = window
        self.tray = QSystemTrayIcon(window)
        self.menu = QMenu()
        self.start_stop = self.menu.addAction('Start', self._toggle)
        self.menu.addSeparator()
        self.destination = self.menu.addAction('')
        self.destination.setEnabled(False)
        self.fps = self.menu.addAction('')
        self.fps.setEnabled(False)
        self.menu.addSeparator()
        self.open_action = self.menu.addAction('Open Gesture', self.open_window)
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction('Quit Gesture', window.quit_app)
        self.menu.aboutToShow.connect(self.refresh)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

        window.engine.stateChanged.connect(self._on_state)
        window.engine.statusReceived.connect(self._on_status)
        QGuiApplication.instance().applicationStateChanged.connect(self._on_app_state)
        self._on_state(window.engine.state)
        self.refresh()

    def show(self) -> None:
        """Show the item and make closing the window keep the engine running."""
        self.tray.show()
        self.window.keep_running_when_closed = True
        QApplication.setQuitOnLastWindowClosed(False)
        # A quit that bypasses quit_app (Dock > Quit, logout) still stops the engine cleanly
        QApplication.instance().aboutToQuit.connect(self.window.engine.stop_and_wait)

    # ------------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------------
    def refresh(self) -> None:
        """Refresh the destination (config may have changed) and the fps line."""
        cfg = self.window.config
        self.destination.setText('{}:{}'.format(cfg.get('osc', 'host'), cfg.get('osc', 'port')))
        running = self.window.engine.is_running()
        self._set_fps(self.window.engine.last_status.get('fps') if running else None)

    def _set_fps(self, fps) -> None:
        text = '--' if fps is None else '{:.0f}'.format(fps)
        self.fps.setText('{} fps'.format(text))
        self._update_tooltip()

    def _on_state(self, state: str) -> None:
        streaming = state in ('starting', 'running')
        self.start_stop.setText('Stop' if streaming else 'Start')
        self.start_stop.setEnabled(state != 'stopping')
        self.tray.setIcon(status_icon(state == 'running'))
        if state in ('stopped', 'stopping'):
            self._set_fps(None)
        self._update_tooltip(state)

    def _on_status(self, msg: dict) -> None:
        self._set_fps(msg.get('fps'))

    def _update_tooltip(self, state: str | None = None) -> None:
        state = state or self.window.engine.state
        label = {'running': 'streaming', 'starting': 'starting', 'stopping': 'stopping'}.get(
            state, 'stopped')
        self.tray.setToolTip('Gesture - {} - {}'.format(label, self.fps.text()))

    # ------------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------------
    def _toggle(self) -> None:
        self.window.toggle_engine()

    def open_window(self) -> None:
        """Show and raise the main window."""
        w = self.window
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()

    def _on_activated(self, reason) -> None:
        # On macOS a click opens the menu; elsewhere a plain click opens the window
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_window()

    def _on_app_state(self, state) -> None:
        # A Dock click re-activates a running app whose window is hidden
        if state != Qt.ApplicationState.ApplicationActive or self.window.isVisible():
            return
        # Launch activation is not a Dock click: the splash hands the window over itself
        splash = getattr(self.window, '_launch_animation', None)
        if splash is not None and splash.is_playing:
            return
        self.open_window()


# ============================================================================
# MENU BAR
# ============================================================================
def _own(window, text: str, shortcut: str | None, slot) -> QAction:
    """A menu-only action (not in window.actions)."""
    a = QAction(text, window)
    if shortcut:
        a.setShortcut(shortcut)
    a.triggered.connect(lambda _checked=False: slot())
    return a


def _build_menu_bar(window) -> dict:
    A = window.actions
    bar = window.menuBar()
    bar.setNativeMenuBar(True)

    A['about'].setMenuRole(QAction.MenuRole.AboutRole)
    A['settings'].setMenuRole(QAction.MenuRole.PreferencesRole)
    A['quit'].setMenuRole(QAction.MenuRole.QuitRole)
    A['docs'].triggered.connect(lambda _checked=False: window.show_pane('help'))

    menus = {}

    def add(title: str, items) -> QMenu:
        m = bar.addMenu(title)
        for item in items:
            if item is None:
                m.addSeparator()
            else:
                m.addAction(item)
        menus[title] = m
        return m

    add('Gesture', [A['about'], None, A['settings'], None, A['quit']])
    add('File', [A['reveal_config'], A['copy_console']])
    add('Engine', [A['start_stop'], A['restart'], None, A['apply'], A['revert']])
    add('View', [A['toggle_console'], A['toggle_osc'], None, A['next_pane'], A['prev_pane']])

    minimize = _own(window, 'Minimize', 'Ctrl+M', window.showMinimized)
    zoom = _own(window, 'Zoom', None,
                lambda: window.showNormal() if window.isMaximized() else window.showMaximized())
    front = _own(window, 'Bring All to Front', None, lambda: (window.show(), window.raise_()))
    window._window_menu_actions = (minimize, zoom, front)
    add('Window', [minimize, zoom, None, front])

    add('Help', [A['docs'], A['setup'], None, A['check_updates']])
    return menus


# ============================================================================
# FOCUS ORDER
# ============================================================================
def _focusable(root: QWidget) -> list:
    return [w for w in [root] + root.findChildren(QWidget)
            if w.focusPolicy() & Qt.FocusPolicy.TabFocus]


def focus_chain(window) -> list:
    """
    The Tab order: toolbar, rail, pane fields top to bottom, pending-change bar, console.

    Args:
        window: The MainWindow.

    Returns:
        Focusable widgets in order.
    """
    chain: list = []
    for root in (window.toolbar, window.rail, window.stack, window._pending_slot, window.console):
        chain.extend(_focusable(root))
    return chain


def apply_focus_order(window) -> None:
    """Set the Tab order to `focus_chain`; call again when the pending bar changes."""
    chain = focus_chain(window)
    for a, b in zip(chain, chain[1:]):
        QWidget.setTabOrder(a, b)


# ============================================================================
# INSTALL
# ============================================================================
def install(window) -> None:
    """
    Build the menu bar, the status item and the focus order.

    Args:
        window: The MainWindow.
    """
    window.menus = _build_menu_bar(window)
    apply_focus_order(window)
    window.status_item = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        window.status_item = StatusItem(window)
        window.status_item.show()
