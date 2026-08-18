#!/usr/bin/env python3
"""
MediaPipe OSC - Application Entry Point

Dispatches between two ways of running the app:
  - With command line arguments: runs the tracking engine (same as main.py)
  - With no arguments: opens the settings launcher window

This is the entry point used to build the macOS .app bundle. The launcher
window relaunches this same executable with CLI arguments to start tracking,
so the GUI and the command line share exactly one code path.
"""

import sys


def _real_args():
    """
    Return the user-supplied arguments

    macOS LaunchServices may append a process serial number argument
    (-psn_0_12345) when an app bundle is opened from Finder. That is not a
    user argument and must not be treated as one.

    Returns:
        List of command line arguments with Finder artifacts removed
    """
    return [arg for arg in sys.argv[1:] if not arg.startswith('-psn')]


def main():
    """Route to the tracking engine or the settings launcher"""
    argv = _real_args()

    if argv:
        import main as engine
        return engine.main(argv) or 0

    from src.gui import run_gui
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
