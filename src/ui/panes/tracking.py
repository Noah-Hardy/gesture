#!/usr/bin/env python3
"""
Tracking Pane

Placeholder until #68 replaces this module's `create()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ._placeholder import placeholder

TITLE = 'Tracking'
BLURB = 'Higher is steadier; lower catches partial bodies. Thresholds apply live.'


def create(window) -> QWidget:
    """
    Build the Tracking pane.

    Args:
        window: The MainWindow (config, engine, actions, toast, ...).

    Returns:
        The pane widget.
    """
    return placeholder(TITLE, BLURB)
