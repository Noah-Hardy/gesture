#!/usr/bin/env python3
"""
Help Pane

Placeholder until #72 replaces this module's `create()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._placeholder import placeholder

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
    return placeholder(TITLE, BLURB)
