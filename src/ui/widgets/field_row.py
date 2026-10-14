"""FieldRow: label column + control + note / warn / error lines + RESTART chip."""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..tokens import Font, Spacing, color
from .display import RestartChip

_M = Spacing.FOCUS_MARGIN


class FieldRow(QWidget):
    """
    One settings row, as rendered by the design's `row()`.

    Layout: a 250px label column (with a 3px dirty marker sitting 11px left
    of the label text, in a reserved gutter), the control, then optional
    lines beneath it: a RESTART chip (only when dirty and restart-class),
    a note (faint), a warning (warn) and an error (error). Rows are 30px
    tall minimum with a hairline underneath.

    Args:
        label: Label text.
        control: The control widget (Toggle, Select, ...). Ownership is taken.
        note: Faint helper text.
        warn: Amber warning text.
        restart: True if changing this value needs an engine restart.
    """

    def __init__(self, label: str, control: QWidget, *, note: str | None = None,
                 warn: str | None = None, restart: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.control = control
        self._restart = restart
        self._dirty = False

        self._label = QLabel(label, self)
        self._label.setFont(Font.ui(13))
        self._label.setWordWrap(True)
        self._label.setFixedWidth(Spacing.LABEL_WIDTH)
        self._label.setMinimumHeight(Spacing.ROW_HEIGHT)
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._label.setStyleSheet(
            f"color: {color('text_2').name()}; background: transparent; padding-top: 6px;")

        self._chip = RestartChip('restart', self)
        self._chip.hide()
        self._note = self._line('faint', note)
        self._warn = self._line('warn', warn)
        self._error = self._line('error', None)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        # Fixed-size controls hug the left; Expanding ones (slider, table, code) fill.
        fill = (control.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
                and control.maximumWidth() > 10000)
        right.addWidget(control, 0, Qt.AlignmentFlag(0) if fill else Qt.AlignmentFlag.AlignLeft)
        right.addWidget(self._chip, 0, Qt.AlignmentFlag.AlignLeft)
        for w in (self._note, self._warn, self._error):
            right.addWidget(w)
        right.addStretch(1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(Spacing.MARKER_GUTTER, 5, 0, 5)
        # the control reserves a 4px focus margin on its left; shave it off the gap
        lay.setSpacing(Spacing.ROW_GAP - _M)
        lay.addWidget(self._label, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(right, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sync()

    @staticmethod
    def _line(tone: str, text: str | None) -> QLabel:
        lab = QLabel(text or '')
        lab.setFont(Font.ui(11))
        lab.setWordWrap(True)
        lab.setContentsMargins(_M, 4, 0, 0)
        lab.setStyleSheet(f"color: {color(tone).name()}; background: transparent;")
        lab.setVisible(bool(text))
        return lab

    # -- state ------------------------------------------------------------
    def is_dirty(self) -> bool:
        return self._dirty

    def set_dirty(self, dirty: bool) -> None:
        """Show the accent marker (and RESTART chip when restart-class)."""
        self._dirty = dirty
        setter = getattr(self.control, 'set_dirty', None)
        if setter:
            setter(dirty)
        self._sync()

    def set_error(self, message: str | None) -> None:
        """Show (or clear) a validation error under the control."""
        self._error.setText(message or '')
        self._error.setVisible(bool(message))
        setter = getattr(self.control, 'set_error', None)
        if setter:
            setter(bool(message))
        self._sync()

    def set_note(self, text: str | None) -> None:
        self._note.setText(text or '')
        self._sync()

    def set_warn(self, text: str | None) -> None:
        self._warn.setText(text or '')
        self._sync()

    def label_text(self) -> str:
        return self._label.text()

    def chip_visible(self) -> bool:
        return not self._chip.isHidden()

    def _sync(self) -> None:
        self._chip.setVisible(self._restart and self._dirty)
        # the design hides the note while an error is showing
        self._note.setVisible(bool(self._note.text()) and self._error.isHidden())
        self._warn.setVisible(bool(self._warn.text()))
        self.setToolTip('Changed — not yet applied' if self._dirty else '')
        self.update()

    def sizeHint(self) -> QSize:
        h = self.layout().sizeHint().height()
        return QSize(560, max(h, Spacing.ROW_HEIGHT + 2 * _M + 2))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), color('hairline'))
        if self._dirty:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent'))
            # marker: 3x14, 2px radius, at the left of the gutter, level with the label
            p.drawRoundedRect(QRectF(0, 5 + 6 + 2, 3, 14), 2, 2)
