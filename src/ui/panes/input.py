#!/usr/bin/env python3
"""
Input Pane

Rendered from the row spec in spec.py by the generic settings renderer.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._render import SettingsPane
from .spec import PANES


def create(window) -> QWidget:
    """
    Build the Input pane.

    Args:
        window: The MainWindow (config, edits, engine, reveal_config, ...).

    Returns:
        The pane widget; it implements `scroll_to_anchor(key)`.
    """
    return SettingsPane(window, PANES['input'])
