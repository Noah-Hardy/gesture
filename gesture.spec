# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Gesture macOS app bundle.

Produces a onedir bundle at dist/Gesture.app. Onedir is required, not onefile:
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
# The MPOSC_* names are the pre-rename spellings, still honoured.
CODESIGN_IDENTITY = (os.environ.get('GESTURE_CODESIGN_IDENTITY')
                     or os.environ.get('MPOSC_CODESIGN_IDENTITY') or None)
ENTITLEMENTS = os.environ.get('GESTURE_ENTITLEMENTS') or os.environ.get('MPOSC_ENTITLEMENTS') or None

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
# rendered on demand into ~/Library/Application Support/Gesture/docs, because
# nothing may be written inside the signed bundle.
_ICON = 'assets/Gesture.icns'
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

# src/__init__.py resolves its exports through a PEP 562 module
# __getattr__ (importlib.import_module) instead of top-level `from
# .x import y` statements, so PyInstaller's static import graph never
# sees these submodules and silently leaves them out of the bundle -
# `from src import ThreadedOSCSender` then fails at runtime with
# ModuleNotFoundError: No module named 'src.osc_sender'. List every
# submodule src/__init__.py's _EXPORTS map can resolve to.
_HIDDEN_IMPORTS = [
    'NDIlib',
    'src.osc_sender',
    'src.osc_protocol',
    'src.pose_utils',
    'src.model_downloader',
    'src.landmarker_processor',
    'src.pose_processor',
    'src.hand_processor',
    'src.holistic_processor',
    'src.config',
    'src.ndi_capture',
    # Engine <-> launcher IPC (#65): main.py imports most of these
    # statically, but the launcher side is only loaded by the UI
    'src.ipc_protocol',
    'src.engine_channel',
    'src.engine_link',
    'src.live_keys',
    'src.live_settings',
    'src.camera_permission',
]

# Qt (PySide6). PyInstaller's PySide6 hook collects the plugins and frameworks
# for every Qt module it can see imported; naming the three modules the UI uses
# here keeps that set to Core/Gui/Widgets even if an import goes indirect.
_HIDDEN_IMPORTS += [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
]

# Safety net on top of the explicit list above (which test_imports.py checks
# against src._EXPORTS): bundle every module under src/ so a lazily imported
# one can never be missing from the frozen app (#86). Only ever adds modules.
def _source_modules(package='src'):
    """Dotted names of every module and package under src/, found on disk

    Walked from the source tree rather than with PyInstaller's
    collect_submodules, which found nothing here (src is not importable from
    its isolated subprocess).
    """
    names = []
    for root, dirs, files in os.walk(package):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        base = root.replace(os.sep, '.')
        if '__init__.py' in files:
            names.append(base)
        names += [f'{base}.{f[:-3]}' for f in files if f.endswith('.py') and f != '__init__.py']
    return sorted(names)


_SRC_MODULES = _source_modules()
_HIDDEN_IMPORTS += _SRC_MODULES

# The same list, shipped as a data file for `gesture --selftest-imports`, which
# imports each name inside the frozen app and so notices a module the bundle
# lost (walking the bundle could only ever list what is present).
os.makedirs('build', exist_ok=True)
with open('build/src_modules.txt', 'w') as _handle:
    _handle.write('\n'.join(sorted(_SRC_MODULES)) + '\n')
datas.append(('build/src_modules.txt', '.'))

# Qt modules the app never touches. PySide6's hook only bundles what the import
# graph reaches, but its binary-dependency scan can still drag these in, and
# QtWebEngine alone is ~200 MB. Budget: no more than +45 MB over the 0.3 bundle.
_QT_EXCLUDES = [
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtWebView',
    'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
    'PySide6.QtQuick3D', 'PySide6.QtQml', 'PySide6.QtQmlCore', 'PySide6.QtQmlModels',
    'PySide6.QtQmlWorkerScript', 'PySide6.QtQuickTest', 'PySide6.QtQuickTemplates2',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtNetwork', 'PySide6.QtSql', 'PySide6.QtTest',
    'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning', 'PySide6.QtLocation',
    'PySide6.QtSensors', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus', 'PySide6.QtRemoteObjects',
    'PySide6.QtScxml', 'PySide6.QtStateMachine', 'PySide6.QtTextToSpeech',
    'PySide6.QtSpatialAudio', 'PySide6.QtHttpServer', 'PySide6.QtConcurrent',
    'PySide6.QtDBus', 'PySide6.QtXml', 'PySide6.QtUiTools', 'PySide6.QtPrintSupport',
    'PySide6.QtAxContainer', 'PySide6.QtVirtualKeyboard',
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=_HIDDEN_IMPORTS,
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
        'tkinter',
        '_tkinter',
        'IPython',
        'jupyter',
        'pandas',
        'setuptools',
        'tensorflow',
        'torch',
    ] + _QT_EXCLUDES,
    noarchive=False,
    optimize=0,
)

# PySide6's hook also copies Qt frameworks and plugins that the three modules
# above never load (they come in as dependencies of image-format and input
# plugins), and the Qt translations. Dropping them is what holds the bundle to
# the +45 MB budget (QtDBus must stay: QtGui links it). Nothing dropped is
# imported by src/ui.
_QT_DROP_FRAMEWORKS = (
    'QtPdf', 'QtQml', 'QtQmlMeta', 'QtQmlModels', 'QtQmlWorkerScript', 'QtQuick',
    'QtVirtualKeyboard', 'QtVirtualKeyboardQml', 'QtNetwork', 'QtOpenGL', 'QtSvg',
)
_QT_DROP_PLUGINS = (
    'libqpdf', 'libqsvg', 'libqsvgicon', 'libqtuiotouchplugin', 'libqtvirtualkeyboardplugin',
)


def _drop_unused_qt(toc):
    """Filter unused Qt frameworks, plugins and translations out of a TOC list"""
    kept = []
    for entry in toc:
        dest = entry[0]
        if 'PySide6/Qt/translations' in dest:
            continue
        # The framework's files, and the top-level symlinks (dest 'QtQml' ->
        # the framework binary) PyInstaller adds for each; a symlink left
        # behind would dangle and fail `codesign --verify --strict`.
        if any(f'PySide6/Qt/lib/{name}.framework/' in dest or dest == name
               or f'/Qt/lib/{name}.framework/' in str(entry[1])
               for name in _QT_DROP_FRAMEWORKS):
            continue
        if '/plugins/' in dest and os.path.basename(dest).split('.')[0] in _QT_DROP_PLUGINS:
            continue
        kept.append(entry)
    return kept


a.binaries = _drop_unused_qt(a.binaries)
a.datas = _drop_unused_qt(a.datas)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='gesture',
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
    name='gesture',
)

app = BUNDLE(
    coll,
    name='Gesture.app',
    icon=_ICON,
    # Deliberately still the pre-rename identifier: 0.2.1's updater only
    # accepts an update signed with exactly this one (src/updater.py
    # BUNDLE_ID), and it also keeps the camera permission macOS granted
    bundle_identifier='net.hardymail.mp-osc',
    info_plist={
        'CFBundleName': 'Gesture',
        'CFBundleDisplayName': 'Gesture',
        'NSCameraUsageDescription':
            'Gesture uses the camera for pose and hand tracking.',
        'NSLocalNetworkUsageDescription':
            'Gesture discovers NDI video sources and sends OSC data on the local network.',
        'NSBonjourServices': ['_ndi._tcp.'],
        # ndi-python 6.x ships macosx_13_0_arm64 wheels, so 13.0 is the real floor.
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
    },
)
