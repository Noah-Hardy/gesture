#!/usr/bin/env python3
"""
Pane Placeholder

The title-and-blurb page each pane module returns until its own issue replaces
it. Later workers replace a pane module's `create()`; this helper then goes
unused and is deleted with the last placeholder.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..tokens import Font, color


def placeholder(title: str, blurb: str) -> QWidget:
    """
    Build a placeholder pane page.

    Args:
        title: Pane title (22px).
        blurb: One-line description shown under the title.

    Returns:
        A QWidget with the title and blurb at the top.
    """
    page = QWidget()
    page.setObjectName('placeholderPane')
    lay = QVBoxLayout(page)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(6)
    head = QLabel(title)
    head.setFont(Font.ui(22, 500))
    head.setStyleSheet('background: transparent;')
    sub = QLabel(blurb)
    sub.setFont(Font.ui(13))
    sub.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
    sub.setWordWrap(True)
    lay.addWidget(head)
    lay.addWidget(sub)
    lay.addStretch(1)
    return page
