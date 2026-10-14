"""
Config: deep-merge, sanitization, env overrides, atomic save, and the
get_config() singleton contract - importing src.config must not read
config.json from disk, only the first get_config() call should.
"""
import json
import os

import pytest

from src.config import Config, app_support_dir, default_config_path, get_config, getenv


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / 'config.json')


def test_missing_file_falls_back_to_defaults(config_path):
    cfg = Config(config_path)
    assert cfg.get('camera', 'buffer_size') == 1


def test_protocol_defaults_to_float(config_path):
    # float is the default since 0.4.0 (legacy is the deprecated 0.2.x format)
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'float'


# 0.3 -> 0.4 default change (#83): 0.3.x wrote "legacy" into nearly every config
def _write(path, data):
    with open(path, 'w') as f:
        json.dump(data, f)


def test_fresh_config_is_float_with_no_notice(config_path):
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'float'
    assert cfg.get('osc', 'protocol_migrated') == '0.4.0'
    assert cfg.get('ui', 'protocol_notice_pending') is False


def test_0_3_legacy_config_is_migrated_once_with_notice(config_path, capsys):
    _write(config_path, {'osc': {'protocol': 'legacy', 'port': 9000}})
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'float'
    assert cfg.get('osc', 'port') == 9000
    assert cfg.get('osc', 'protocol_migrated') == '0.4.0'
    assert cfg.get('ui', 'protocol_notice_pending') is True
    assert 'Legacy (0.2.x)' in capsys.readouterr().out


def test_migration_does_not_repeat_after_save(config_path):
    _write(config_path, {'osc': {'protocol': 'legacy'}})
    cfg = Config(config_path)
    cfg.save()
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'float'
    # The user now deliberately goes back to legacy: it must stick
    cfg.set('osc', 'protocol', 'legacy')
    cfg.set('ui', 'protocol_notice_pending', False)
    cfg.save()
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'legacy'
    assert cfg.get('ui', 'protocol_notice_pending') is False


def test_explicit_legacy_after_migration_is_kept(config_path):
    _write(config_path, {'osc': {'protocol': 'legacy', 'protocol_migrated': '0.4.0'}})
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'legacy'
    assert cfg.get('ui', 'protocol_notice_pending') is False


@pytest.mark.parametrize('protocol', ['json', 'float'])
def test_migration_leaves_json_and_float_alone(config_path, protocol):
    _write(config_path, {'osc': {'protocol': protocol}})
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == protocol
    assert cfg.get('osc', 'protocol_migrated') == '0.4.0'
    assert cfg.get('ui', 'protocol_notice_pending') is False


def test_pre_0_3_config_without_protocol_key_is_migrated_with_notice(config_path):
    # Before 0.3 there was no osc.protocol; such a file was sending the
    # legacy format, so it gets the same notice as an explicit legacy
    _write(config_path, {'osc': {'port': 9000}})
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'float'
    assert cfg.get('ui', 'protocol_notice_pending') is True


def test_sanitize_normalizes_known_protocol(config_path):
    with open(config_path, 'w') as f:
        json.dump({'osc': {'protocol': ' JSON '}}, f)
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'json'


@pytest.mark.parametrize('bad', ['udp-carrier-pigeon', 42, None, ''])
def test_sanitize_clamps_unknown_protocol_to_default(config_path, bad):
    with open(config_path, 'w') as f:
        json.dump({'osc': {'protocol': bad}}, f)
    cfg = Config(config_path)
    assert cfg.get('osc', 'protocol') == 'float'


def test_include_prereleases_defaults_to_false(config_path):
    # Stable users should not be offered pre-release builds unless they
    # opt in - see Settings -> General -> Include pre-release builds.
    cfg = Config(config_path)
    assert cfg.get('updates', 'include_prereleases') is False


def test_deep_merge_preserves_sibling_defaults(config_path):
    with open(config_path, 'w') as f:
        json.dump({'osc': {'port': 9999}}, f)
    cfg = Config(config_path)
    assert cfg.get('osc', 'port') == 9999
    # Untouched sibling keys in the same section must survive the merge
    assert cfg.get('osc', 'host') == Config.DEFAULT_CONFIG['osc']['host']


def test_two_instances_do_not_share_mutable_defaults(config_path, tmp_path):
    # Regression: DEFAULT_CONFIG.copy() is shallow, so mutating a nested
    # dict on one instance used to leak into every other Config() built in
    # the same process, including the DEFAULT_CONFIG class attribute itself.
    path_a = str(tmp_path / 'a.json')
    with open(path_a, 'w') as f:
        json.dump({'osc': {'port': 1111}}, f)
    Config(path_a)

    path_b = str(tmp_path / 'b.json')
    cfg_b = Config(path_b)
    assert cfg_b.get('osc', 'port') == Config.DEFAULT_CONFIG['osc']['port']
    assert Config.DEFAULT_CONFIG['osc']['port'] != 1111


def test_sanitize_repairs_invalid_buffer_size(config_path):
    with open(config_path, 'w') as f:
        json.dump({'camera': {'buffer_size': 0}}, f)
    cfg = Config(config_path)
    assert cfg.get('camera', 'buffer_size') == 1


def test_ndi_bandwidth_defaults_to_lowest(config_path):
    cfg = Config(config_path)
    assert cfg.get('camera', 'ndi_bandwidth') == 'lowest'


def test_sanitize_repairs_unknown_ndi_bandwidth(config_path):
    with open(config_path, 'w') as f:
        json.dump({'camera': {'ndi_bandwidth': 'ultra'}}, f)
    cfg = Config(config_path)
    assert cfg.get('camera', 'ndi_bandwidth') == 'lowest'


def test_sanitize_keeps_highest_ndi_bandwidth(config_path):
    with open(config_path, 'w') as f:
        json.dump({'camera': {'ndi_bandwidth': 'highest'}}, f)
    cfg = Config(config_path)
    assert cfg.get('camera', 'ndi_bandwidth') == 'highest'


def test_sanitize_floors_max_pending_frames(config_path):
    with open(config_path, 'w') as f:
        json.dump({'performance': {'max_pending_frames': 0}}, f)
    cfg = Config(config_path)
    assert cfg.get('performance', 'max_pending_frames') == 1


def test_sanitize_repairs_non_numeric_buffer_size(config_path):
    with open(config_path, 'w') as f:
        json.dump({'camera': {'buffer_size': 'not a number'}}, f)
    cfg = Config(config_path)
    assert cfg.get('camera', 'buffer_size') == 1


def test_sanitize_raises_undersized_queue_to_floor(config_path):
    # Regression: configs saved before 0.1.8 could have queue_size below one
    # frame's worth of 'all'-mode OSC traffic (14 messages), causing
    # mid-frame drops even under normal load (issue #49).
    with open(config_path, 'w') as f:
        json.dump({'osc': {'queue_size': 5}}, f)
    cfg = Config(config_path)
    assert cfg.get('osc', 'queue_size') == Config.MIN_OSC_QUEUE_SIZE


def test_sanitize_preserves_larger_queue_size(config_path):
    with open(config_path, 'w') as f:
        json.dump({'osc': {'queue_size': 64}}, f)
    cfg = Config(config_path)
    assert cfg.get('osc', 'queue_size') == 64


def test_sanitize_repairs_non_numeric_queue_size(config_path):
    with open(config_path, 'w') as f:
        json.dump({'osc': {'queue_size': 'not a number'}}, f)
    cfg = Config(config_path)
    assert cfg.get('osc', 'queue_size') == Config.MIN_OSC_QUEUE_SIZE


def test_retired_preview_window_keys_are_dropped_on_load(config_path):
    with open(config_path, 'w') as f:
        json.dump({'display': {'show_window': False, 'window_title': 'My Custom Title',
                               'mirror_preview': True}}, f)
    cfg = Config(config_path)
    assert 'show_window' not in cfg.get('display')
    assert 'window_title' not in cfg.get('display')
    assert cfg.get('display', 'mirror_preview') is True


def test_retired_keys_are_not_in_the_defaults():
    assert 'show_window' not in Config.DEFAULT_CONFIG['display']
    assert 'window_title' not in Config.DEFAULT_CONFIG['display']


def test_corrupt_json_falls_back_to_defaults(config_path):
    with open(config_path, 'w') as f:
        f.write('{not valid json')
    cfg = Config(config_path)
    assert cfg.get('camera', 'buffer_size') == 1


def test_env_override_applies_and_coerces_type(config_path, monkeypatch):
    monkeypatch.delenv('MP_OSC_PORT', raising=False)
    monkeypatch.setenv('GESTURE_OSC_PORT', '5555')
    cfg = Config(config_path)
    assert cfg.get('osc', 'port') == 5555
    assert isinstance(cfg.get('osc', 'port'), int)


def test_legacy_env_name_still_works(config_path, monkeypatch):
    monkeypatch.delenv('GESTURE_OSC_PORT', raising=False)
    monkeypatch.setenv('MP_OSC_PORT', '5556')
    cfg = Config(config_path)
    assert cfg.get('osc', 'port') == 5556


def test_gesture_env_name_wins_over_legacy(config_path, monkeypatch):
    monkeypatch.setenv('GESTURE_OSC_HOST', '10.0.0.2')
    monkeypatch.setenv('MP_OSC_HOST', '10.0.0.1')
    cfg = Config(config_path)
    assert cfg.get('osc', 'host') == '10.0.0.2'


def test_getenv_order_and_default(monkeypatch):
    monkeypatch.delenv('GESTURE_X', raising=False)
    monkeypatch.delenv('MPOSC_X', raising=False)
    assert getenv('GESTURE_X', 'MPOSC_X', default='d') == 'd'
    monkeypatch.setenv('MPOSC_X', 'legacy')
    assert getenv('GESTURE_X', 'MPOSC_X') == 'legacy'
    monkeypatch.setenv('GESTURE_X', 'new')
    assert getenv('GESTURE_X', 'MPOSC_X') == 'new'


# ----------------------------------------------------------------------------
# app_support_dir: the one-time mp-osc -> Gesture data folder migration
# ----------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    support = tmp_path / 'Library' / 'Application Support'
    support.mkdir(parents=True)
    return support


def test_app_support_dir_fresh_install(home):
    path = app_support_dir()
    assert path == str(home / 'Gesture')
    assert os.path.isdir(path)
    assert not (home / 'mp-osc').exists()


def test_app_support_dir_migrates_the_old_folder(home):
    old = home / 'mp-osc'
    (old / 'models').mkdir(parents=True)
    (old / 'config.json').write_text('{"osc": {"port": 4321}}')

    path = app_support_dir()
    assert path == str(home / 'Gesture')
    assert not old.exists()
    assert (home / 'Gesture' / 'config.json').read_text() == '{"osc": {"port": 4321}}'
    assert (home / 'Gesture' / 'models').is_dir()

    # Idempotent: a second call finds the migrated folder and leaves it be
    assert app_support_dir() == path


def test_app_support_dir_keeps_gesture_when_both_exist(home):
    (home / 'mp-osc').mkdir()
    (home / 'mp-osc' / 'config.json').write_text('old')
    (home / 'Gesture').mkdir()
    (home / 'Gesture' / 'config.json').write_text('new')

    assert app_support_dir() == str(home / 'Gesture')
    # Never merged or overwritten
    assert (home / 'Gesture' / 'config.json').read_text() == 'new'
    assert (home / 'mp-osc' / 'config.json').read_text() == 'old'


def test_app_support_dir_falls_back_to_the_old_folder_when_rename_fails(home, monkeypatch):
    import src.config as config_module
    (home / 'mp-osc').mkdir()

    def failing_rename(src, dst):
        raise PermissionError('nope')

    monkeypatch.setattr(config_module.os, 'rename', failing_rename)
    assert app_support_dir() == str(home / 'mp-osc')
    assert not (home / 'Gesture').exists()


def test_app_support_dir_tolerates_losing_the_rename_race(home, monkeypatch):
    # The launcher and the engine it spawned both try to migrate; the
    # loser's rename fails because the winner already moved the folder
    import src.config as config_module
    (home / 'mp-osc').mkdir()
    real_rename = os.rename

    def racing_rename(src, dst):
        real_rename(src, dst)          # the other process wins...
        raise FileNotFoundError(src)   # ...and ours finds nothing to move

    monkeypatch.setattr(config_module.os, 'rename', racing_rename)
    assert app_support_dir() == str(home / 'Gesture')


def test_frozen_config_path_uses_app_support_dir(home, monkeypatch):
    import src.config as config_module
    monkeypatch.setattr(config_module.sys, 'frozen', True, raising=False)
    assert default_config_path() == str(home / 'Gesture' / 'config.json')


def test_env_override_bool_coercion(config_path, monkeypatch):
    monkeypatch.setenv('GESTURE_SHOW_FPS', 'true')
    cfg = Config(config_path)
    assert cfg.get('performance', 'show_fps') is True


def test_env_override_accepts_pre_rename_name(config_path, monkeypatch):
    monkeypatch.setenv('MP_CAMERA_ID', '2')
    cfg = Config(config_path)
    assert cfg.get('camera', 'device_id') == 2


def test_env_override_gesture_name_wins_over_pre_rename(config_path, monkeypatch):
    monkeypatch.setenv('GESTURE_CAMERA_ID', '3')
    monkeypatch.setenv('MP_CAMERA_ID', '2')
    cfg = Config(config_path)
    assert cfg.get('camera', 'device_id') == 3


def test_save_is_atomic_and_round_trips(config_path):
    cfg = Config(config_path)
    cfg.set('osc', 'port', 7777)
    cfg.save()
    assert os.path.exists(config_path)
    # No leftover temp file
    leftovers = [f for f in os.listdir(os.path.dirname(config_path)) if f.startswith('.config.')]
    assert leftovers == []

    reloaded = Config(config_path)
    assert reloaded.get('osc', 'port') == 7777


def test_get_config_returns_the_same_instance(monkeypatch, config_path):
    import src.config as config_module
    monkeypatch.setattr(config_module, '_config', None)
    monkeypatch.setattr(config_module, 'default_config_path', lambda: config_path)
    a = get_config()
    b = get_config()
    assert a is b


def test_sanitize_drops_keys_of_the_removed_solutions_path(config_path):
    """#35: legacy-only keys in an old config are dropped on load, not carried forward"""
    with open(config_path, 'w') as f:
        json.dump({
            'mediapipe': {'model_complexity': 2, 'enable_segmentation': True,
                          'smooth_landmarks': False, 'pose_model_type': 'full'},
            'hand': {'model_complexity': 0, 'num_hands': 1},
            'performance': {'force_legacy': True, 'force_cpu': True},
        }, f)
    cfg = Config(config_path)
    for section, key in (('mediapipe', 'model_complexity'), ('mediapipe', 'enable_segmentation'),
                         ('mediapipe', 'smooth_landmarks'), ('hand', 'model_complexity'),
                         ('performance', 'force_legacy')):
        assert key not in cfg.get(section)
    # Neighbouring keys are untouched
    assert cfg.get('mediapipe', 'pose_model_type') == 'full'
    assert cfg.get('hand', 'num_hands') == 1
    assert cfg.get('performance', 'force_cpu') is True
