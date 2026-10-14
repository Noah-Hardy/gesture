"""The Help pane in the main window opens topics and anchors (#72)"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.config import Config  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    w = MainWindow(Config(str(tmp_path / "config.json")))
    yield w
    w.close()
    app.processEvents()


def test_help_pane_is_the_help_view(window):
    window.show_pane("help", "troubleshooting")
    assert window.current_pane() == "help"
    assert window.pane("help").current_slug == "troubleshooting"


def test_help_pane_accepts_slug_and_anchor(window):
    window.show_pane("help", "osc-reference#float-format")
    assert window.pane("help").current_slug == "osc-reference"
