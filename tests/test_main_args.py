"""main.py CLI argument parsing and config-override wiring for the
--preview-window and --ipc-* flags (#65) and the config-override wiring."""
import pytest

from src.config import Config
from main import apply_config_overrides, parse_args


def test_preview_window_defaults_to_off():
    assert parse_args(['pose']).preview_window is False


def test_preview_window_flag():
    assert parse_args(['--preview-window', 'pose']).preview_window is True


def test_old_preview_spelling_still_parses():
    assert parse_args(['--preview', 'pose']).preview_window is True
    assert parse_args(['--no-preview', 'pose']).preview_window is False


def test_ipc_flags_default_to_standalone():
    args = parse_args(['pose'])
    assert args.ipc_fd is None
    assert args.ipc_shm is None


def test_ipc_flags_parse():
    args = parse_args(['--ipc-fd', '7', '--ipc-shm', 'gst123abc', 'all'])
    assert args.ipc_fd == 7
    assert args.ipc_shm == 'gst123abc'


def test_overrides_no_longer_touch_display_window_keys(tmp_path):
    config = Config(str(tmp_path / 'config.json'))
    apply_config_overrides(parse_args(['--preview-window', 'pose']), config)
    assert 'show_window' not in config.get('display')


# ============================================================================
# --osc-protocol (0.3.0)
# ============================================================================
def test_osc_protocol_defaults_to_none():
    assert parse_args(['pose']).osc_protocol is None


@pytest.mark.parametrize('protocol', ['legacy', 'json', 'float'])
def test_osc_protocol_overrides_config(tmp_path, protocol):
    config = Config(str(tmp_path / 'config.json'))
    config.set('osc', 'protocol', 'json' if protocol != 'json' else 'legacy')
    apply_config_overrides(parse_args(['--osc-protocol', protocol, 'pose']), config)
    assert config.get('osc', 'protocol') == protocol


def test_osc_protocol_absent_leaves_config_alone(tmp_path):
    config = Config(str(tmp_path / 'config.json'))
    assert config.get('osc', 'protocol') == 'float'
    config.set('osc', 'protocol', 'json')
    apply_config_overrides(parse_args(['pose']), config)
    assert config.get('osc', 'protocol') == 'json'


def test_osc_protocol_rejects_unknown_values():
    with pytest.raises(SystemExit):
        parse_args(['--osc-protocol', 'xml', 'pose'])
