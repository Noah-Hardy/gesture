"""First-run wizard and protocol notice tests (#69). Run offscreen."""

import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui import tokens  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.ui.overlays import first_run  # noqa: E402


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


@pytest.fixture(autouse=True)
def _stub_platform(monkeypatch):
    monkeypatch.setattr(first_run, 'is_adhoc_signed', lambda: False)
    monkeypatch.setattr(first_run, 'camera_status', lambda: 'undetermined')


class FakeEngine:
    def __init__(self):
        self.started = 0

    def is_running(self):
        return False

    def start(self):
        self.started += 1


@pytest.fixture
def made(app, tmp_path):
    windows = []

    def build(contents=None):
        path = str(tmp_path / 'config.json')
        if contents is not None:
            with open(path, 'w') as f:
                json.dump(contents, f)
        w = MainWindow(Config(path))
        w.show()
        app.processEvents()
        w.real_engine = w.engine
        w.engine = FakeEngine()
        windows.append(w)
        return w, path

    yield build
    for w in windows:
        w._save_timer.stop()
        w.real_engine.stop_and_wait()
        w.hide()
        w.deleteLater()


def click(card, name):
    w = card.findChild(QWidget, name)
    (w.click if hasattr(w, 'click') else w.clicked.emit)()


def saved(path):
    with open(path) as f:
        return json.load(f)


def test_fresh_config_shows_wizard(made):
    w, _ = made()
    assert not w.first_run.wizard.isHidden()
    assert w.first_run.notice.isHidden()
    assert w.overlay_host().isVisible()


def test_finish_isadora_writes_once_and_starts(made, monkeypatch):
    w, path = made()
    saves = []
    orig = w.config.save
    monkeypatch.setattr(w.config, 'save', lambda: (saves.append(1), orig()))
    w.config.set('osc', 'protocol', 'json')
    fr = w.first_run
    fr._go(fr._steps.index('receiver'))
    fr._pick('Isadora')
    fr._go(fr._steps.index('ready'))
    click(fr.wizard, 'frPrimary')
    data = saved(path)
    assert (data['osc']['host'], data['osc']['port'], data['osc']['protocol']) == ('127.0.0.1', 1234, 'float')
    assert data['ui']['first_run_complete'] is True
    assert len(saves) == 1
    assert w.engine.started == 1
    assert fr.wizard.isHidden()


def test_non_isadora_leaves_protocol(made):
    w, path = made()
    w.config.set('osc', 'protocol', 'json')
    fr = w.first_run
    fr._go(fr._steps.index('receiver'))
    fr._pick('TouchDesigner')
    fr._go(fr._steps.index('ready'))
    click(fr.wizard, 'frPrimary')
    data = saved(path)
    assert data['osc']['port'] == 7000 and data['osc']['protocol'] == 'json'


def test_not_yet_keeps_settings_without_starting(made):
    w, path = made()
    fr = w.first_run
    fr._go(fr._steps.index('ready'))
    click(fr.wizard, 'frSecondary')
    assert saved(path)['ui']['first_run_complete'] is True
    assert w.engine.started == 0


def test_skip_marks_complete(made):
    w, path = made()
    click(w.first_run.wizard, 'frSecondary')
    assert w.first_run.wizard.isHidden()
    assert saved(path)['ui']['first_run_complete'] is True
    assert w.engine.started == 0


def test_camera_request_updates_status(made, monkeypatch):
    w, _ = made()
    fr = w.first_run
    monkeypatch.setattr(first_run, 'request_camera', lambda ctx, cb: cb('granted'))
    fr._go(fr._steps.index('camera'))
    click(fr.wizard, 'frPrimary')
    assert fr._camera == 'granted'
    texts = [lab.text() for lab in fr.wizard.findChildren(QLabel)]
    assert 'Camera access granted' in texts


def test_unsigned_build_inserts_quarantine_step(made, monkeypatch):
    monkeypatch.setattr(first_run, 'is_adhoc_signed', lambda: True)
    w, _ = made()
    assert w.first_run._steps[1] == 'quarantine' and len(w.first_run._steps) == 5


def test_setup_action_reopens(made):
    w, _ = made()
    click(w.first_run.wizard, 'frSecondary')
    assert w.first_run.wizard.isHidden()
    w.actions['setup'].trigger()
    assert not w.first_run.wizard.isHidden() and w.first_run._step == 0


def test_completed_config_shows_nothing(made):
    w, _ = made({'osc': {'protocol': 'float'}, 'ui': {'first_run_complete': True}})
    assert w.first_run.wizard.isHidden() and w.first_run.notice.isHidden()


def test_upgrader_sees_only_notice(made):
    w, _ = made({'osc': {'protocol': 'legacy', 'port': 9000}})
    assert w.first_run.wizard.isHidden()
    assert not w.first_run.notice.isHidden()
    assert w.config.get('ui', 'first_run_complete') is True


def test_keep_legacy_sets_legacy_and_clears_flag(made):
    w, path = made({'osc': {'protocol': 'legacy'}})
    click(w.first_run.notice, 'frKeepLegacy')
    data = saved(path)
    assert data['osc']['protocol'] == 'legacy'
    assert data['ui']['protocol_notice_pending'] is False
    assert w.first_run.notice.isHidden()


def test_use_float_clears_flag(made):
    w, path = made({'osc': {'protocol': 'legacy'}})
    click(w.first_run.notice, 'frUseFloat')
    data = saved(path)
    assert data['osc']['protocol'] == 'float'
    assert data['ui']['protocol_notice_pending'] is False
