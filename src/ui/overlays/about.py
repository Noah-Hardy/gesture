#!/usr/bin/env python3
"""
About overlay

A native macOS-style About window: a QDialog with the standard title bar and
window controls, no OK button, closed with Cmd+W. One instance at a time;
choosing About again raises the open one.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from src import docs
from src.ui.icon import mark_pixmap
from src.ui.tokens import Font, color

TAGLINE = 'MediaPipe pose and hand tracking, streamed over OSC'
PLATFORM_LINE = 'Apple Silicon · macOS 13+'
LICENSE_LINE = 'Apache License 2.0 · Noah Hardy'
GITHUB_URL = 'https://github.com/Noah-Hardy/gesture'
GITHUB_LABEL = 'github.com/Noah-Hardy/gesture'
ICON_SIZE = 76


# ============================================================================
# DIALOG
# ============================================================================
class AboutDialog(QDialog):
    """
    The About window.

    Args:
        parent: The MainWindow (keeps the dialog above it).
        version: App version string shown in the title line.
    """

    def __init__(self, parent=None, version: str = ''):
        super().__init__(parent)
        self.setWindowTitle('About Gesture')
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName('aboutDialog')
        self.setStyleSheet(
            f"QDialog#aboutDialog {{ background: {color('bg_window').name()}; }}"
            " QLabel { background: transparent; }")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 30, 40, 30)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        icon = QLabel()
        dpr = self.devicePixelRatioF() or 2.0
        icon.setPixmap(mark_pixmap(ICON_SIZE, dpr))
        icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(14)

        self.title_label = self._label(f'Gesture {version}'.strip(), 17, 500, 'text')
        lay.addWidget(self.title_label)
        lay.addSpacing(3)
        lay.addWidget(self._label(TAGLINE, 12, 400, 'dim'))
        lay.addSpacing(14)
        lay.addWidget(self._label(PLATFORM_LINE, 12, 400, 'faint'))
        lay.addWidget(self._label(LICENSE_LINE, 12, 400, 'faint'))
        lay.addSpacing(14)

        self.link_label = self._label(
            f'<a href="{GITHUB_URL}" style="color:{color("accent").name()};'
            f' text-decoration:none">{GITHUB_LABEL}</a>', 12, 400, 'accent')
        self.link_label.setTextFormat(Qt.TextFormat.RichText)
        self.link_label.setOpenExternalLinks(True)
        lay.addWidget(self.link_label)

        self.setFixedWidth(460)
        self.adjustSize()
        self.setFixedSize(self.size())

        # Cmd+W (Ctrl+W elsewhere) closes it, like any native About window
        self._close_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Close), self)
        self._close_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._close_shortcut.activated.connect(self.close)

    @staticmethod
    def _label(text: str, px: int, weight: int, tone: str) -> QLabel:
        label = QLabel(text)
        label.setFont(Font.ui(px, weight))
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        label.setStyleSheet(f"color: {color(tone).name()};")
        return label


# ============================================================================
# INSTALL
# ============================================================================
def show_about(window) -> AboutDialog:
    """
    Open the About window, or raise it if it is already open.

    Args:
        window: The MainWindow.

    Returns:
        The AboutDialog.
    """
    dlg = getattr(window, '_about_dialog', None)
    if dlg is not None:
        try:
            dlg.isVisible()
        except RuntimeError:  # underlying C++ object already deleted
            dlg = None
    if dlg is None:
        dlg = AboutDialog(window, docs.app_version())
        dlg.destroyed.connect(lambda *_: setattr(window, '_about_dialog', None))
        window._about_dialog = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def install(window) -> None:
    """
    Wire `window.actions['about']` to the About window.

    Args:
        window: The MainWindow.
    """
    window._about_dialog = None
    action = window.actions.get('about')
    if action is not None:
        action.triggered.connect(lambda *_: show_about(window))
