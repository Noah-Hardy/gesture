#!/usr/bin/env python3
"""
Help Pane

The in-app documentation (#72): `HelpView` from `src/ui/help_view.py`.
`window.show_pane('help', anchor)` accepts `<slug>` or `<slug>#<anchor>`,
the same target a `help://<slug>#<anchor>` link names.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ..help_view import HelpView

TITLE = 'Help'
BLURB = 'Documentation for Gesture.'


def create(window) -> QWidget:
    """
    Build the Help pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget.
    """
    view = HelpView()

    def scroll_to_anchor(target: str) -> None:
        slug, _, anchor = target.partition('#')
        view.open(slug, anchor or None)

    view.scroll_to_anchor = scroll_to_anchor
    return view
