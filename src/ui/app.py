#!/usr/bin/env python3
"""
Qt Application Entry

Creates the QApplication (design-token stylesheet, application icon), the
shared Config and the MainWindow, and runs the event loop. `app.py` with no
arguments calls `run()`.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import sys
import time

import psutil

from PySide6.QtWidgets import QApplication

from src.config import get_config

from . import tokens
from .icon import app_icon
from .launch import maybe_play
from .main_window import MainWindow


def build_app(argv: list[str] | None = None) -> QApplication:
    """
    Get or create the QApplication with the Gesture look.

    Args:
        argv: Process arguments (defaults to sys.argv).

    Returns:
        The QApplication.
    """
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName('Gesture')
    app.setOrganizationName('Gesture')
    app.setWindowIcon(app_icon())
    tokens.apply(app)
    return app


def run() -> int:
    """
    Launch the Gesture window and block until it quits.

    Returns:
        The process exit code.
    """
    app = build_app()
    config = get_config()
    window = MainWindow(config)
    # Ready time counts from process creation, so a slow (frozen, cold)
    # start plays the launch animation and a warm one skips it (#74)
    try:
        ready_ms = (time.time() - psutil.Process().create_time()) * 1000.0
    except Exception:
        ready_ms = None
    maybe_play(window, config, ready_ms)
    return app.exec()
