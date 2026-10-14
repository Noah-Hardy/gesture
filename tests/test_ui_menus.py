"""Menu bar, shortcuts, status item, close/quit behaviour (#71). Offscreen."""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QAction, QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import menus, tokens  # noqa: E402
from src.ui.engine import EngineController  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402

SHORTCUTS = {
    'start_stop': 'Ctrl+Return', 'restart': 'Ctrl+R', 'apply': 'Ctrl+S', 'revert': 'Ctrl+Z',
    'settings': 'Ctrl+,', 'toggle_console': 'Ctrl+Shift+C', 'toggle_osc': 'Ctrl+Shift+O',
    'next_pane': 'Ctrl+]', 'prev_pane': 'Ctrl+[', 'reveal_config': 'Ctrl+Shift+,',
    'copy_console': 'Ctrl+Shift+L', 'docs': 'Ctrl+?', 'quit': 'Ctrl+Q',
}


class FakeEngine(EngineController):
    """Engine whose process control is recorded instead of spawning anything."""

    def __init__(self, config):
        super().__init__(config)
        self.running = False
        self.calls = []

    def is_running(self):
        return self.running

    def start(self):
        self.calls.append('start')
        self.running = True
        self._set_state('running')

    def stop(self):
        self.calls.append('stop')
        self.running = False
        self._set_state('stopped')

    def stop_and_wait(self, timeout=12.0):
        self.calls.append('stop_and_wait')
        self.running = False
        self._set_state('stopped')
        return True


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


@pytest.fixture
def window(app, tmp_path):
    cfg = Config(str(tmp_path / 'config.json'))
    w = MainWindow(cfg, engine=FakeEngine(cfg))
    w.show()
    app.processEvents()
    yield w
    w._save_timer.stop()
    w.hide()
    w.deleteLater()


@pytest.fixture
def status(window):
    item = menus.StatusItem(window)
    item.show()
    yield item
    window.keep_running_when_closed = False
    item.tray.hide()


def test_shortcuts_match_table_and_trigger(window):
    for key, seq in SHORTCUTS.items():
        assert window.actions[key].shortcut() == QKeySequence(seq), key
    assert window.actions['check_updates'].shortcut().isEmpty()
    for key in ('apply', 'revert', 'toggle_osc', 'docs'):
        hits = []
        window.actions[key].triggered.connect(lambda *_a, k=key: hits.append(k))
        window.actions[key].trigger()
        assert hits == [key]


def test_menu_structure(window):
    bar = window.menuBar()
    assert [a.text() for a in bar.actions()] == ['Gesture', 'File', 'Engine', 'View', 'Window', 'Help']

    def texts(title):
        return [a.text() for a in window.menus[title].actions() if not a.isSeparator()]

    assert texts('Engine') == ['Start', 'Restart Engine', 'Apply Pending Changes', 'Revert Changes']
    assert 'Documentation' in texts('Help') and 'Check for Updates...' in texts('Help')
    assert 'Set Up...' in texts('Help')
    assert texts('Window')[0] == 'Minimize'
    roles = {k: window.actions[k].menuRole() for k in ('about', 'settings', 'quit')}
    assert roles == {'about': QAction.MenuRole.AboutRole,
                     'settings': QAction.MenuRole.PreferencesRole,
                     'quit': QAction.MenuRole.QuitRole}


def test_docs_opens_help_and_engine_menu_follows_state(window):
    window.actions['docs'].trigger()
    assert window.current_pane() == 'help'
    start = window.actions['start_stop']
    window.engine.start()
    assert start.text() == 'Stop'
    window.engine.stop()
    assert start.text() == 'Start'


def test_close_hides_and_keeps_engine_running(app, window, status):
    window.engine.start()
    window.close()
    assert not window.isVisible()
    assert window.engine.calls == ['start']
    assert window.engine.is_running()


def test_quit_stops_engine_then_exits(app, window, status, monkeypatch):
    quit_calls = []
    monkeypatch.setattr(QApplication, 'quit',
                        staticmethod(lambda: quit_calls.append(window.engine.calls[:])))
    window.engine.start()
    window.actions['quit'].trigger()
    assert window.engine.calls == ['start', 'stop_and_wait']
    assert quit_calls == [['start', 'stop_and_wait']]


def test_close_without_status_item_quits(app, window, monkeypatch):
    quit_calls = []
    monkeypatch.setattr(QApplication, 'quit', staticmethod(lambda: quit_calls.append(1)))
    window.engine.start()
    window.close()
    assert window.engine.calls == ['start', 'stop_and_wait'] and quit_calls == [1]


def test_status_item_menu_and_fps(window, status):
    cfg = window.config
    assert status.start_stop.text() == 'Start'
    assert status.fps.text() == '-- fps'
    assert status.destination.text() == '{}:{}'.format(cfg.get('osc', 'host'), cfg.get('osc', 'port'))
    assert not status.destination.isEnabled() and not status.fps.isEnabled()
    window.engine.start()
    assert status.start_stop.text() == 'Stop'
    window.engine.last_status = {'fps': 29.4}
    window.engine.statusReceived.emit({'fps': 29.4})
    assert status.fps.text() == '29 fps'
    cfg.set('osc', 'port', 9123)
    status.refresh()
    assert status.destination.text().endswith(':9123')
    window.engine.stop()
    assert status.fps.text() == '-- fps' and status.start_stop.text() == 'Start'
    status.start_stop.trigger()
    assert window.engine.calls[-1] == 'start'
    window.hide()
    status.open_action.trigger()
    assert window.isVisible()


def test_status_icon_accent_vs_dim(app):
    def dominant(streaming):
        img = menus.status_pixmap(streaming).toImage()
        px = [img.pixelColor(x, y) for x in range(img.width()) for y in range(img.height())]
        solid = [c for c in px if c.alpha() == 255]
        assert solid
        c = solid[len(solid) // 2]
        return c.red(), c.green(), c.blue()

    assert dominant(True) == (0x3D, 0xDC, 0x84) or max(
        abs(a - b) for a, b in zip(dominant(True), (0x3D, 0xDC, 0x84))) <= 1
    assert max(abs(a - b) for a, b in zip(dominant(False), (0x2F, 0x78, 0x56))) <= 1


def test_focus_chain_order(window):
    chain = menus.focus_chain(window)

    def first(root):
        return next(i for i, w in enumerate(chain) if w is root or root.isAncestorOf(w))

    order = [first(r) for r in (window.toolbar, window.rail, window.stack, window.console)]
    assert order == sorted(order) and len(set(order)) == 4
    assert all(isinstance(w, QWidget) and w.focusPolicy() & Qt.FocusPolicy.TabFocus for w in chain)


def test_status_item_pops_own_menu_instead_of_native_context_menu(window, status, monkeypatch):
    # setContextMenu() crashes on macOS (Qt calls -[NSEvent clickCount] on a non-mouse event)
    assert status.tray.contextMenu() is None
    popups = []
    monkeypatch.setattr(status.menu, 'popup', lambda pos, *a: popups.append(pos))
    reasons = QSystemTrayIcon.ActivationReason
    status.tray.activated.emit(reasons.Trigger)
    status.tray.activated.emit(reasons.Context)
    assert len(popups) == 2
    status.tray.activated.emit(reasons.MiddleClick)
    assert len(popups) == 2
    window.hide()
    status.tray.activated.emit(reasons.DoubleClick)
    assert window.isVisible()


def test_popup_position_anchors_under_item_with_cursor_fallback(status, monkeypatch):
    monkeypatch.setattr(status.tray, 'geometry', lambda: QRect(100, 0, 24, 24))
    assert status.popup_position() == QPoint(100, 24)
    monkeypatch.setattr(status.tray, 'geometry', lambda: QRect())
    monkeypatch.setattr(menus.QCursor, 'pos', staticmethod(lambda: QPoint(7, 9)))
    assert status.popup_position() == QPoint(7, 9)


def test_app_window_icon_not_set_on_macos(app, monkeypatch):
    from src.ui import app as ui_app
    calls = []
    monkeypatch.setattr(QApplication, 'setWindowIcon', staticmethod(lambda i: calls.append(i)))
    monkeypatch.setattr(ui_app.sys, 'platform', 'darwin')
    ui_app.build_app()
    assert calls == []
    monkeypatch.setattr(ui_app.sys, 'platform', 'linux')
    ui_app.build_app()
    assert len(calls) == 1


def test_second_click_closes_menu_and_dismiss_click_does_not_reopen(status, monkeypatch):
    popups = []
    monkeypatch.setattr(status.menu, 'popup', lambda pos, *a: popups.append(pos))
    visible = [False]
    monkeypatch.setattr(status.menu, 'isVisible', lambda: visible[0])
    hides = []
    monkeypatch.setattr(status.menu, 'hide', lambda: hides.append(1))
    trigger = QSystemTrayIcon.ActivationReason.Trigger
    status.tray.activated.emit(trigger)
    assert len(popups) == 1
    visible[0] = True
    status.tray.activated.emit(trigger)  # second click closes
    assert hides == [1] and len(popups) == 1
    visible[0] = False
    status._on_menu_hidden()
    status.tray.activated.emit(trigger)  # the dismissing click, delivered late
    assert len(popups) == 1
    monkeypatch.setattr(menus, 'REOPEN_GUARD_S', 0.0)
    status.tray.activated.emit(trigger)
    assert len(popups) == 2
