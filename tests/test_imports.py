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
