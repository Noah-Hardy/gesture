"""
Config: deep-merge, sanitization, env overrides, atomic save, and the
get_config() singleton contract - importing src.config must not read
config.json from disk, only the first get_config() call should.
"""
import json
import os

import pytest

from src.config import Config, get_config


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / 'config.json')


def test_missing_file_falls_back_to_defaults(config_path):
    cfg = Config(config_path)
    assert cfg.get('camera', 'buffer_size') == 1


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


def test_sanitize_repairs_non_numeric_buffer_size(config_path):
    with open(config_path, 'w') as f:
        json.dump({'camera': {'buffer_size': 'not a number'}}, f)
    cfg = Config(config_path)
    assert cfg.get('camera', 'buffer_size') == 1


def test_corrupt_json_falls_back_to_defaults(config_path):
    with open(config_path, 'w') as f:
        f.write('{not valid json')
    cfg = Config(config_path)
    assert cfg.get('camera', 'buffer_size') == 1


def test_env_override_applies_and_coerces_type(config_path, monkeypatch):
    monkeypatch.setenv('MP_OSC_PORT', '5555')
    cfg = Config(config_path)
    assert cfg.get('osc', 'port') == 5555
    assert isinstance(cfg.get('osc', 'port'), int)


def test_env_override_bool_coercion(config_path, monkeypatch):
    monkeypatch.setenv('MP_SHOW_FPS', 'true')
    cfg = Config(config_path)
    assert cfg.get('performance', 'show_fps') is True


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
