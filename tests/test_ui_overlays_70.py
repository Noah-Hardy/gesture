"""About and update overlays (#70). Run offscreen with a fake UpdateController."""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.overlays import about as about_mod  # noqa: E402
from src.ui.overlays import update as update_mod  # noqa: E402
from src.updater import Release  # noqa: E402

RELEASE = Release(
    tag='v9.9.9', version='9.9.9', name='Gesture 9.9.9',
    notes='## What is new\n\n- Faster **tracking**\n- Fixed `OSC` bug\n',
    html_url='https://github.com/Noah-Hardy/gesture/releases/tag/v9.9.9',
    zip_url='https://example.invalid/g.zip', zip_name='g.zip', zip_size=43200000,
    sha_url='https://example.invalid/g.sha256', prerelease=False)


class FakeController:
    def __init__(self, out, config, tag):
        self.out = out
        self.tag = tag
        self.busy = False
        self.checks = []
        self.installs = []
        self.cancelled = False

    def check_async(self, manual=False):
        self.checks.append(manual)

    def install_async(self, release):
        self.installs.append(release)

    def cancel(self):
        self.cancelled = True


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(Config(str(tmp_path / 'config.json')))
    w.show()
    app.processEvents()
    yield w
    w._save_timer.stop()
    w.engine.stop_and_wait()
    w.hide()


@pytest.fixture
def overlay(window):
    window.update_overlay.updater = FakeController(None, None, 't')
    return window.update_overlay


def emit(overlay, **payload):
    overlay._bridge.put(('update', payload))
    QApplication.processEvents()


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------
def test_about_single_instance_and_close_shortcut(window, app):
    window.actions['about'].trigger()
    dlg = window._about_dialog
    assert dlg is not None and dlg.isVisible()
    assert not dlg.windowFlags() & Qt.WindowType.FramelessWindowHint
    window.actions['about'].trigger()
    assert window._about_dialog is dlg
    assert dlg._close_shortcut.key() == QKeySequence(QKeySequence.StandardKey.Close)
    assert 'Gesture' in dlg.title_label.text()
    assert about_mod.GITHUB_LABEL in dlg.link_label.text()
    dlg._close_shortcut.activated.emit()
    app.processEvents()
    app.sendPostedEvents(None, 0)
    assert window._about_dialog is None
    window.actions['about'].trigger()
    assert window._about_dialog is not None
    window._about_dialog.close()


# ---------------------------------------------------------------------------
# Update checks
# ---------------------------------------------------------------------------
def test_manual_check_none_and_error_toast(window, overlay):
    toasts = []
    window.toast = lambda text, ms=2800: toasts.append(text)
    window.actions['check_updates'].trigger()
    assert overlay.updater.checks == [True]
    emit(overlay, kind='none', manual=True, message='')
    emit(overlay, kind='error', manual=True, message='GitHub rate limit reached.')
    emit(overlay, kind='error', manual=False, message='silent')
    assert "You're up to date" in toasts
    assert 'GitHub rate limit reached.' in toasts
    assert 'silent' not in toasts


def test_persist_applied(window, overlay):
    emit(overlay, kind='none', manual=False, persist={'etag': 'abc'})
    assert window.config.get('updates', 'etag') == 'abc'


def test_available_opens_card_and_rail(window, overlay):
    emit(overlay, kind='available', release=RELEASE, manual=False)
    card = overlay.card
    assert card is not None and card.isVisible()
    assert card.title_label.text() == 'Gesture 9.9.9 is available'
    assert '41.2 MB' in card.subtitle_label.text()
    assert card.subtitle_label.text().startswith('You have ')
    assert 'tracking' in card.notes.toPlainText()
    assert window.rail.update_card.isVisible()
    assert card.button_texts() == ['Skip this version', 'Later', 'Install & relaunch']


def test_later_then_rail_click_reopens(window, overlay):
    emit(overlay, kind='available', release=RELEASE)
    overlay.card.laterRequested.emit()
    QApplication.processEvents()
    assert overlay.card is None
    window.updateCardClicked.emit()
    assert overlay.card is not None


def test_skip_records_version(window, overlay):
    emit(overlay, kind='available', release=RELEASE)
    overlay.card.skipRequested.emit()
    assert window.config.get('updates', 'skipped_version') == 'v9.9.9'
    assert overlay.card is None
    assert not window.rail.update_card.isVisible()


def test_install_progress_phases(window, overlay):
    emit(overlay, kind='available', release=RELEASE)
    overlay.card.installRequested.emit()
    assert overlay.updater.installs == [RELEASE]
    emit(overlay, kind='progress', done=50, total=100)
    card = overlay.card
    assert card.status_label.text() == 'Downloading'
    assert card.percent_label.text() == '50%'
    assert card.bar.fraction() == 0.5
    assert card.button_texts() == ['Cancel']
    card.laterRequested.emit()  # cannot dismiss mid-install
    assert overlay.card is card
    emit(overlay, kind='verifying', phase='signature')
    assert card.status_label.text() == 'Verifying checksum & signature'
    card.cancelRequested.emit()
    assert overlay.updater.cancelled


def test_ready_stops_engine_and_spawns_installer(window, overlay, monkeypatch, app):
    calls = []
    monkeypatch.setattr(update_mod, 'spawn_installer', lambda staged: calls.append(('spawn', staged)))
    monkeypatch.setattr(window.engine, 'stop_and_wait', lambda *a, **k: calls.append(('stop',)) or True)
    quits = []
    monkeypatch.setattr(app, 'quit', lambda: quits.append(1))
    emit(overlay, kind='available', release=RELEASE)
    emit(overlay, kind='ready', staged='STAGED')
    assert overlay.card.status_label.text() == 'Verified & ready to install'
    app.processEvents()
    assert calls == [('stop',), ('spawn', 'STAGED')]
    assert quits == [1]


def test_ready_spawn_failure_shows_failed(window, overlay, monkeypatch, app):
    def boom(staged):
        raise OSError('nope')
    monkeypatch.setattr(update_mod, 'spawn_installer', boom)
    monkeypatch.setattr(window.engine, 'stop_and_wait', lambda *a, **k: True)
    monkeypatch.setattr(app, 'quit', lambda: pytest.fail('must not quit'))
    emit(overlay, kind='available', release=RELEASE)
    emit(overlay, kind='ready', staged='S')
    app.processEvents()
    assert overlay.card.title_label.text() == 'Update failed'
    assert 'nope' in overlay.card.status_label.text()


def test_failed_payload(window, overlay):
    emit(overlay, kind='available', release=RELEASE)
    emit(overlay, kind='failed', message='Checksum mismatch')
    card = overlay.card
    assert card.status_label.text() == 'Checksum mismatch'
    assert card.button_texts() == ['Close', 'Open release page']


def test_install_payloads_without_card_are_ignored(window, overlay):
    emit(overlay, kind='progress', done=1, total=2)
    emit(overlay, kind='failed', message='x')
    assert overlay.card is None


def test_launch_check_honours_setting(window, overlay, monkeypatch):
    monkeypatch.setattr(update_mod, 'cleanup_stale', lambda: None)
    overlay.launch_check()
    assert overlay.updater.checks == [False]
    window.config.set('updates', 'check_on_launch', False)
    overlay.launch_check()
    assert overlay.updater.checks == [False]


def test_busy_manual_check_does_not_start(window, overlay):
    overlay.updater.busy = True
    overlay.check(manual=True)
    assert overlay.updater.checks == []
