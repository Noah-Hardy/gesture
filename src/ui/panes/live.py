#!/usr/bin/env python3
"""
Live Pane

Placeholder until #66 replaces this module's `create()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._placeholder import placeholder

TITLE = 'Live'
BLURB = 'Preview, meters and OSC activity.'


def create(window) -> QWidget:
    """
    Build the Live pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget.
    """
    return placeholder(TITLE, BLURB)
