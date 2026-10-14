"""Widget kit tests (#77). Run offscreen; skipped when PySide6 cannot import."""

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.ui import gallery, tokens  # noqa: E402
from src.ui.widgets import (  # noqa: E402
    FieldRow, RangeSlider, SegmentedChoice, Select, Sparkline, TextField, Toggle,
)


@pytest.fixture(scope='module')
def app():
    a = QApplication.instance() or QApplication([])
    tokens.apply(a)
    return a


def test_stylesheet_builds_from_tokens(app):
    qss = tokens.build_stylesheet()
    assert tokens.PALETTE['bg_window'] in qss
    assert '{{' not in qss and '}}' not in qss


def test_palette_matches_spec():
    p = tokens.PALETTE
    assert p['bg_deep'] == '#0B0D11' and p['accent'] == '#3DDC84'
    assert p['error'] == '#E5695B' and p['hairline'] == '#22262F'


def test_gallery_constructs(app):
    root = gallery.build_gallery()
    assert root.findChildren(Toggle)
    assert any(r.chip_visible() for r in root.findChildren(FieldRow))


@pytest.mark.parametrize('key', [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_toggle_keys(app, key):
    t = Toggle(False)
    t.show()
    QTest.keyClick(t, key)
    assert t.isChecked()
    QTest.keyClick(t, key)
    assert not t.isChecked()


def test_range_slider_steps(app):
    s = RangeSlider(0.5)
    s.show()
    s.setFocus()
    seen = []
    s.valueChanged.connect(seen.append)
    QTest.keyClick(s._track, Qt.Key.Key_Right)
    assert s.value() == pytest.approx(0.55)
    QTest.keyClick(s._track, Qt.Key.Key_Left)
    QTest.keyClick(s._track, Qt.Key.Key_Left)
    assert s.value() == pytest.approx(0.45)
    s.setValue(0.97)
    assert s.value() == pytest.approx(0.95)
    s.setValue(5)
    assert s.value() == 1.0
    assert s._readout.text() == '1.00'


def test_segmented_and_select(app):
    c = SegmentedChoice(['a', 'b', 'c'], 'a')
    c.show()
    QTest.keyClick(c, Qt.Key.Key_Right)
    assert c.current() == 'b'
    sel = Select(['x', 'y'], 'x')
    sel.setCurrentText('y')
    assert sel.currentText() == 'y'
    assert TextField('1').text() == '1'


def test_field_row_states(app):
    row = FieldRow('Force CPU', Toggle(), restart=True, note='n')
    row.show()
    assert not row.chip_visible()
    row.set_dirty(True)
    assert row.chip_visible()
    row.set_error('bad')
    assert not row._note.isVisible()
    row.set_error(None)
    row.set_dirty(False)
    assert not row.chip_visible()


def test_sparkline_ring_buffer(app):
    s = Sparkline()
    for i in range(50):
        s.push(i / 50)
    assert len(s.values()) == 22


def test_tab_reaches_every_control(app):
    win = gallery.build_window()
    content = win.widget()
    content.setFixedWidth(860)
    win.show()
    win.activateWindow()
    QApplication.processEvents()
    expected = {
        w for w in content.findChildren(QWidget)
        if w.isVisible() and w.isEnabled()
        and (w.focusPolicy() & Qt.FocusPolicy.TabFocus)
    }
    assert len(expected) > 20
    visited = set()
    first = None
    win.setFocus()
    for _ in range(len(expected) + 5):
        content.focusNextChild()
        fw = QApplication.focusWidget()
        if fw is None:
            continue
        if first is None:
            first = fw
        visited.add(fw)
    missing = expected - visited
    assert not missing, [type(w).__name__ for w in missing]
