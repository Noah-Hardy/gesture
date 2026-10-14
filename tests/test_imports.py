"""
Guards the lazy-import contract: `import src.docs` must not drag in
mediapipe, cv2 or NDIlib. Those only get imported the first time some other
`src.*` name that actually needs them is accessed (src/__init__.py's PEP 562
module __getattr__).

Runs the check in a subprocess with a fresh interpreter rather than checking
sys.modules in-process: once mediapipe is imported anywhere in this test
run - by test_pose_utils.py importing cv2/numpy, say - there is no way to
un-import it, so an in-process check would become order-dependent on
however pytest happens to collect files.
"""
import subprocess
import sys

_CHECK_SCRIPT = """
import sys
import src.docs
assert 'mediapipe' not in sys.modules, 'importing src.docs imported mediapipe'
assert 'cv2' not in sys.modules, 'importing src.docs imported cv2'
print('OK')
"""


def test_importing_docs_does_not_import_mediapipe_or_cv2():
    result = subprocess.run(
        [sys.executable, '-c', _CHECK_SCRIPT],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip() == 'OK'


def test_package_getattr_resolves_known_export():
    import src
    # First access imports src.osc_sender lazily and caches it on the package
    assert src.ThreadedOSCSender.__name__ == 'ThreadedOSCSender'


def test_package_getattr_raises_attributeerror_for_unknown_name():
    import src
    try:
        src.this_name_does_not_exist
    except AttributeError:
        pass
    else:
        raise AssertionError("expected AttributeError for an unknown src.* name")


def test_spec_hiddenimports_cover_every_lazy_export():
    # PyInstaller can't see the importlib calls behind src.__getattr__, so a
    # submodule missing from gesture.spec's hiddenimports is left out of the
    # bundle and only fails in the frozen app (#86).
    import ast
    import os
    import src
    spec_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'gesture.spec')
    with open(spec_path) as f:
        tree = ast.parse(f.read())
    hidden = set()
    for node in ast.walk(tree):
        # The explicit list lives in `_HIDDEN_IMPORTS = [...]` (later `+=`
        # additions are computed, not literal, and are ignored here)
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.List)
                and any(isinstance(t, ast.Name) and t.id == '_HIDDEN_IMPORTS' for t in node.targets)):
            hidden |= set(ast.literal_eval(node.value))
    needed = {f'src.{name}' for name in src._EXPORTS.values()}
    assert not needed - hidden, f"add to gesture.spec hiddenimports: {sorted(needed - hidden)}"


def test_selftest_imports_flag_passes_from_source():
    # The frozen-app smoke test (`gesture --selftest-imports`); from source it
    # proves every src.* module imports and matplotlib is pinned to Agg.
    import os
    root = os.path.dirname(os.path.dirname(__file__))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    env.pop('MPLBACKEND', None)
    result = subprocess.run(
        [sys.executable, os.path.join(root, 'app.py'), '--selftest-imports'],
        capture_output=True, text=True, timeout=120, env=env, cwd=root,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert ' 0 failed' in result.stdout
