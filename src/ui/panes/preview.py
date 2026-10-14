#!/usr/bin/env python3
"""
Preview Pane

Placeholder until #68 replaces this module's `create()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._placeholder import placeholder

TITLE = 'Preview'
BLURB = 'Display only - never affects OSC.'


def create(window) -> QWidget:
    """
    Build the Preview pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget.
    """
    return placeholder(TITLE, BLURB)
