#!/usr/bin/env python3
"""
Gesture - Application Entry Point

Dispatches between two ways of running the app:
  - With command line arguments: runs the tracking engine (same as main.py)
  - With no arguments: opens the Gesture window (src.ui.app)

This is the entry point used to build the macOS .app bundle. The window
relaunches this same executable with CLI arguments to start tracking,
so the GUI and the command line share exactly one code path.
"""

import os
import sys

# matplotlib stays in the bundle only for mediapipe's drawing_utils. Pin the
# non-interactive backend before anything can import it, so it never probes
# for a Qt/Tk backend (and can't pull a second Qt into the process). Set here
# rather than in a runtime hook so the engine path (app.py with arguments)
# and the window path are covered by the same line.
os.environ.setdefault('MPLBACKEND', 'Agg')


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


def selftest_imports():
    """
    Import every module the frozen app can reach, then report

    Release smoke test (`Gesture.app/Contents/MacOS/gesture --selftest-imports`).
    A module PyInstaller left out of the bundle only fails when something
    imports it at runtime, which is how #86 shipped; this imports them all up
    front. Not part of the user-facing CLI.

    Returns:
        0 when everything imported, 1 otherwise
    """
    import importlib
    import pkgutil

    import src

    failures = []
    names = ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'cv2', 'mediapipe',
             'NDIlib', 'matplotlib.pyplot', 'pythonosc', 'main']
    # Frozen: the list written at build time by gesture.spec, so a module the
    # bundle lost is still on the list and fails here. (Walking the bundle would
    # only ever list what is already in it.) From source: walk the package.
    manifest = os.path.join(getattr(sys, '_MEIPASS', ''), 'src_modules.txt')
    if getattr(sys, 'frozen', False) and os.path.exists(manifest):
        with open(manifest) as handle:
            names += [line.strip() for line in handle if line.strip()]
    else:
        names += [m.name for m in pkgutil.walk_packages(src.__path__, 'src.')]
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:  # report every miss, not just the first
            failures.append(f'{name}: {type(exc).__name__}: {exc}')

    import matplotlib
    if matplotlib.get_backend().lower() != 'agg':
        failures.append(f'matplotlib backend is {matplotlib.get_backend()!r}, expected Agg')

    for line in failures:
        print('FAIL', line)
    print(f'{len(names)} modules checked, {len(failures)} failed')
    return 1 if failures else 0


def main():
    """Route to the tracking engine or the Gesture window"""
    argv = _real_args()

    if argv == ['--selftest-imports']:
        return selftest_imports()

    if argv:
        import main as engine
        return engine.main(argv) or 0

    from src.ui.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
