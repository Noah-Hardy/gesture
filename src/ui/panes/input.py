#!/usr/bin/env python3
"""
Input Pane

Placeholder until #67 replaces this module's `create()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._placeholder import placeholder

TITLE = 'Input'
BLURB = 'Changes apply on restart.'


def create(window) -> QWidget:
    """
    Build the Input pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget.
    """
    return placeholder(TITLE, BLURB)
