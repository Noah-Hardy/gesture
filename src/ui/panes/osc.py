#!/usr/bin/env python3
"""
OSC Output Pane

Placeholder until #68 replaces this module's `create()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._placeholder import placeholder

TITLE = 'OSC Output'
BLURB = 'Host, port and queue apply live. Tracking mode needs a restart.'


def create(window) -> QWidget:
    """
    Build the OSC Output pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget.
    """
    return placeholder(TITLE, BLURB)
