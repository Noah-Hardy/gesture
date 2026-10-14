#!/usr/bin/env python3
"""
Advanced Pane

Placeholder until #68 replaces this module's `create()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._placeholder import placeholder

TITLE = 'Advanced'
BLURB = 'Backend changes apply on restart.'


def create(window) -> QWidget:
    """
    Build the Advanced pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget.
    """
    return placeholder(TITLE, BLURB)
