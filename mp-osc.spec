# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the MP-OSC macOS app bundle.

Produces a onedir bundle at dist/MP-OSC.app. Onedir is required, not onefile:
the launcher GUI relaunches this same frozen executable as a subprocess with
CLI arguments, and a onefile build would re-extract the ~500MB payload on every
launch instead of sharing one sys._MEIPASS directory.

Build with scripts/build_app.sh, not by calling pyinstaller directly. The .task
models are not kept in the repository, and that script downloads them into
src/tasks/ before they get bundled.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


def _project_version():
    """Read the version from pyproject.toml.

    Parsed by hand rather than with tomllib: keeps working if the build is
    ever pinned back to a Python older than 3.11. scripts/release.sh reads the same line the same way, so
    the archive name and CFBundleShortVersionString cannot drift apart.
    """
    with open('pyproject.toml') as handle:
        for line in handle:
            if line.startswith('version'):
                return line.split('"')[1]
    raise SystemExit('could not read version from pyproject.toml')


VERSION = _project_version()

# Signing identity for a distributable build. Unset means an ad-hoc signature,
# which runs on this machine but is rejected by Gatekeeper anywhere else.
# scripts/release.sh sets both of these when a Developer ID is available.
CODESIGN_IDENTITY = os.environ.get('MPOSC_CODESIGN_IDENTITY') or None
ENTITLEMENTS = os.environ.get('MPOSC_ENTITLEMENTS') or None

# MediaPipe ships its graph assets (.binarypb) and baked-in models (.tflite) as
# package data. They are loaded by path at runtime, so they must be collected.
datas = collect_data_files('mediapipe')

# Landmarker models resolved at runtime via sys._MEIPASS/src/tasks/<name>.task
# (see src/model_downloader._bundled_tasks_dir). They are gitignored downloads,
# so fail loudly rather than shipping a bundle that has to fetch them itself.
EXPECTED_MODELS = [
    'pose_landmarker_lite.task',
    'pose_landmarker_full.task',
    'pose_landmarker_heavy.task',
    'hand_landmarker.task',
    'holistic_landmarker.task',
]
_missing = [m for m in EXPECTED_MODELS if not os.path.exists(os.path.join('src/tasks', m))]
if _missing:
    raise SystemExit(
        'Missing landmarker models in src/tasks/: ' + ', '.join(_missing) +
        '\nBuild with ./scripts/build_app.sh, which downloads them first.'
    )

datas += [('src/tasks', 'src/tasks')]

# Operator documentation, resolved at runtime via sys._MEIPASS/docs (see
# src.docs.docs_dir). Markdown is the shipped form; the browser HTML is
# rendered on demand into ~/Library/Application Support/mp-osc/docs, because
# nothing may be written inside the signed bundle.
_ICON = 'assets/MP-OSC.icns'
if not os.path.exists(_ICON):
    raise SystemExit(
        f'Missing app icon: {_ICON}\nGenerate it with: uv run python scripts/make_icon.py'
    )
if not os.path.isdir('docs') or not os.listdir('docs'):
    raise SystemExit('Missing docs/ directory - operator documentation must ship with the app')

datas += [('docs', 'docs')]

# ndi-python keeps libndi.dylib inside the NDIlib package next to the extension
# module. collect_dynamic_libs finds it there; the fallback below guards against
# a future wheel layout change silently producing an NDI-less bundle.
binaries = collect_dynamic_libs('NDIlib')
if not binaries:
    import NDIlib as _ndilib

    _libndi = os.path.join(os.path.dirname(_ndilib.__file__), 'libndi.dylib')
    if not os.path.exists(_libndi):
        raise SystemExit('libndi.dylib not found; cannot build a working bundle')
    binaries = [(_libndi, 'NDIlib')]

# Returns [] for mediapipe today: its native code lives in Python extension
# modules that Analysis picks up through the import graph. Kept so any plain
# .dylib added by a future mediapipe release is collected too.
binaries += collect_dynamic_libs('mediapipe')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=['NDIlib'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # NOTE: matplotlib is deliberately NOT excluded. mediapipe's __init__ imports
    # mediapipe.python.solutions, whose drawing_utils.py does an unconditional
    # `import matplotlib.pyplot as plt` -- excluding it breaks `import mediapipe`.
    excludes=[
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'IPython',
        'jupyter',
        'pandas',
        'setuptools',
        'tensorflow',
        'torch',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='mp-osc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=CODESIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='mp-osc',
)

app = BUNDLE(
    coll,
    name='MP-OSC.app',
    icon=_ICON,
    bundle_identifier='net.hardymail.mp-osc',
    info_plist={
        'NSCameraUsageDescription':
            'MP-OSC uses the camera for pose and hand tracking.',
        'NSLocalNetworkUsageDescription':
            'MP-OSC discovers NDI video sources and sends OSC data on the local network.',
        'NSBonjourServices': ['_ndi._tcp.'],
        # ndi-python 6.x ships macosx_13_0_arm64 wheels, so 13.0 is the real floor.
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
    },
)
