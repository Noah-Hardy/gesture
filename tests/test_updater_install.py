"""
The update swap script and the MP-OSC.app -> Gesture.app rename it performs.

The generated script is run for real against throwaway bundles in tmp_path.
Its final `/usr/bin/open` is swapped for an echo so no test launches
anything; everything else (the wait loop, mv, ditto fallback, rollback) is
the exact text the updater writes.
"""
import os
import subprocess

import pytest

from src.updater import (APP_NAME, BUNDLE_ID, LEGACY_APP_NAME, _install_destination,
                         _write_install_script)


def make_bundle(path, marker):
    os.makedirs(os.path.join(path, 'Contents'))
    with open(os.path.join(path, 'Contents', 'marker'), 'w') as f:
        f.write(marker)


def marker_of(path):
    with open(os.path.join(path, 'Contents', 'marker')) as f:
        return f.read()


def run_swap(tmp_path, target_app, dest_app=None):
    """Stage a 'new' bundle, write the script, run it; return its stdout"""
    updates_dir = tmp_path / 'updates'
    updates_dir.mkdir(exist_ok=True)
    extract_dir = tmp_path / 'apps' / '.Gesture-update-1'
    staged = extract_dir / APP_NAME
    make_bundle(str(staged), 'new')
    zip_path = updates_dir / 'Gesture-9.9.9-macos-arm64.zip'
    zip_path.write_text('zip')

    script = _write_install_script(str(updates_dir), str(staged), str(target_app),
                                   str(extract_dir), str(zip_path), dest_app=dest_app)
    with open(script) as f:
        text = f.read()
    assert text.count('/usr/bin/open "$DEST"') == 1
    with open(script, 'w') as f:
        f.write(text.replace('/usr/bin/open "$DEST"', 'echo "opened $DEST"'))

    # A PID that has already exited, so the wait loop falls straight through
    exited = subprocess.Popen(['/usr/bin/true'])
    exited.wait()
    result = subprocess.run(['/bin/sh', script, str(exited.pid)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def apps(tmp_path):
    d = tmp_path / 'apps'
    d.mkdir()
    return d


# ----------------------------------------------------------------------------
# _install_destination
# ----------------------------------------------------------------------------

def test_bundle_id_is_unchanged_by_the_rename():
    # 0.2.1's updater verifies updates against this exact identifier
    assert BUNDLE_ID == 'net.hardymail.mp-osc'


def test_legacy_bundle_is_renamed(apps):
    target = apps / LEGACY_APP_NAME
    make_bundle(str(target), 'old')
    assert _install_destination(str(target)) == str(apps / APP_NAME)


def test_legacy_bundle_stays_put_when_gesture_app_exists(apps):
    target = apps / LEGACY_APP_NAME
    make_bundle(str(target), 'old')
    make_bundle(str(apps / APP_NAME), 'other')
    assert _install_destination(str(target)) == str(target)


def test_gesture_bundle_updates_in_place(apps):
    target = apps / APP_NAME
    make_bundle(str(target), 'old')
    assert _install_destination(str(target)) == str(target)


def test_custom_bundle_name_updates_in_place(apps):
    target = apps / 'My Tracker.app'
    make_bundle(str(target), 'old')
    assert _install_destination(str(target)) == str(target)


# ----------------------------------------------------------------------------
# The script itself
# ----------------------------------------------------------------------------

def test_swap_renames_mp_osc_app_to_gesture_app(tmp_path, apps):
    target = apps / LEGACY_APP_NAME
    make_bundle(str(target), 'old')
    out = run_swap(tmp_path, target, _install_destination(str(target)))

    assert not target.exists()
    assert marker_of(str(apps / APP_NAME)) == 'new'
    assert f'opened {apps / APP_NAME}' in out
    # No backup or staging leftovers
    assert sorted(os.listdir(apps)) == [APP_NAME]


def test_swap_updates_in_place_when_gesture_app_appeared_after_staging(tmp_path, apps):
    target = apps / LEGACY_APP_NAME
    make_bundle(str(target), 'old')
    dest = _install_destination(str(target))
    # Someone put a Gesture.app there between staging and the swap
    make_bundle(str(apps / APP_NAME), 'other')
    out = run_swap(tmp_path, target, dest)

    assert marker_of(str(target)) == 'new'
    assert marker_of(str(apps / APP_NAME)) == 'other'
    assert f'opened {target}' in out


def test_swap_in_place_for_gesture_app(tmp_path, apps):
    target = apps / APP_NAME
    make_bundle(str(target), 'old')
    out = run_swap(tmp_path, target)

    assert marker_of(str(target)) == 'new'
    assert f'opened {target}' in out
    assert sorted(os.listdir(apps)) == [APP_NAME]
