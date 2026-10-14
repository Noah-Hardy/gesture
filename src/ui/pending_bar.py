#!/usr/bin/env python3
"""
Pending-Change Bar

The strip above the console that summarises unsaved settings edits and offers
Revert / Apply. `install(window)` creates the EditModel's bar, hands it to
`window.set_pending_bar()` while edits exist, and wires the apply / revert
actions and the toolbar's restart chip.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .edits import EditModel
from .tokens import Font, Radius, color
from .widgets import Button
from .widgets._paint import body_rect
from .widgets.controls import _aa

#: (background, top line) per bar state
BG_LIVE, LINE_LIVE = '#1B2A22', '#2F785688'
BG_RESTART, LINE_RESTART = '#2A251C', '#E5A54B55'
BG_INVALID, LINE_INVALID = '#2A1C1C', '#E5695B55'


# ============================================================================
# WIDGETS
# ============================================================================
class _ApplyButton(Button):
    """Accent-outlined Apply button (grey while disabled)."""

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        _aa(p)
        r = body_rect(self)
        on = self.isEnabled()
        edge = color('accent' if on else 'faint')
        if on and (self._hover or self.isDown()):
            fill = QColor(color('accent'))
            fill.setAlpha(0x18)
            p.setBrush(fill)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(edge, 1))
        p.drawRoundedRect(QRectF(r).adjusted(.5, .5, -.5, -.5), Radius.CONTROL, Radius.CONTROL)
        p.setFont(Font.ui(12, 500))
        p.setPen(edge)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


class PendingBar(QWidget):
    """
    The bar itself; a view over an EditModel.

    Args:
        edits: The model.
        is_running: Callable returning True while the engine is running
            (decides "Apply & restart").

    Signals:
        applyRequested(): The Apply button was clicked.
    """

    applyRequested = Signal()

    def __init__(self, edits: EditModel, is_running=lambda: False, parent: QWidget | None = None):
        super().__init__(parent)
        self.edits = edits
        self._is_running = is_running
        self._bg = BG_LIVE
        self._line = LINE_LIVE

        self.summary = QLabel()
        self.summary.setFont(Font.ui(12, 500))
        self.detail = QLabel()
        self.detail.setFont(Font.ui(11))
        self.message = QLabel()
        self.message.setFont(Font.ui(11))
        for lab, tone in ((self.summary, 'text'), (self.detail, 'dim'), (self.message, 'error')):
            lab.setStyleSheet('color: {}; background: transparent;'.format(color(tone).name()))
            lab.setTextFormat(Qt.TextFormat.PlainText)

        text = QVBoxLayout()
        text.setSpacing(3)
        text.setContentsMargins(0, 0, 0, 0)
        for lab in (self.summary, self.detail, self.message):
            text.addWidget(lab)

        self.revert_button = Button('Revert')
        self.apply_button = _ApplyButton('Apply')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 14, 8)
        lay.setSpacing(8)
        lay.addLayout(text, 1)
        lay.addWidget(self.revert_button)
        lay.addWidget(self.apply_button)

        self.revert_button.clicked.connect(edits.revert)
        self.apply_button.clicked.connect(self.applyRequested)
        edits.changed.connect(self.refresh)
        edits.reverted.connect(self.refresh)
        edits.applied.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        """Re-read the model and update text, colours and the Apply button."""
        dirty = self.edits.dirty_list()
        n = len(dirty)
        restart = sum(1 for d in dirty if d['restart'])
        invalid = sum(1 for d in dirty if d['error'])

        s = '{} {} pending'.format(n, 'change' if n == 1 else 'changes')
        if restart:
            s += ' · {} {} a restart'.format(restart, 'needs' if restart == 1 else 'need')
        else:
            s += ' · applies live'
        self.summary.setText(s)
        names = '   '.join('{} · {}'.format(d['pane_title'], d['label']) for d in dirty[:3])
        if n > 3:
            names += '   +{} more'.format(n - 3)
        self.detail.setText(names)
        if invalid:
            self.message.setText('{} {} invalid — fix before applying'.format(
                invalid, 'field is' if invalid == 1 else 'fields are'))
        self.message.setVisible(bool(invalid))

        if invalid:
            self._bg, self._line = BG_INVALID, LINE_INVALID
        elif restart:
            self._bg, self._line = BG_RESTART, LINE_RESTART
        else:
            self._bg, self._line = BG_LIVE, LINE_LIVE
        self.apply_button.setText('Apply & restart' if restart and self._is_running() else 'Apply')
        self.apply_button.setEnabled(not invalid)
        self.apply_button.updateGeometry()
        self.update()

    def bg_name(self) -> str:
        """Current background colour (hex)."""
        return self._bg

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self._bg))
        p.fillRect(QRectF(0, 0, self.width(), 1), QColor(self._line))


# ============================================================================
# INSTALL
# ============================================================================
def install(window) -> None:
    """
    Wire the model into the shell: bar visibility, toast, apply / revert
    actions (⌘S / ⌘Z) and the toolbar's restart chip.

    Args:
        window: The MainWindow; `window.edits` must be an EditModel.
    """
    edits: EditModel = window.edits
    bar = PendingBar(edits, window.engine.is_running)
    window.pending_bar = bar

    def sync(*_a):
        has = edits.is_dirty()
        window.set_restart_required(has and edits.restart_count() > 0)
        window.set_pending_bar(bar if has else None)
        bar.refresh()

    def on_applied(_fids):
        sync()
        window.refresh_readouts()

    edits.changed.connect(sync)
    edits.reverted.connect(sync)
    edits.applied.connect(on_applied)
    window.engine.stateChanged.connect(lambda _s: bar.refresh())

    def do_apply():
        restart_class = edits.restart_count() > 0
        was_running = window.engine.is_running()
        if edits.apply() is None:
            return
        window.toast('Saved · restarting the engine' if restart_class and was_running
                     else 'Saved to config.json', 2800)

    bar.applyRequested.connect(do_apply)
    window.actions['apply'].triggered.connect(do_apply)
    window.actions['revert'].triggered.connect(edits.revert)
    sync()
