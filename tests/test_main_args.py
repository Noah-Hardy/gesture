"""main.py CLI argument parsing and config-override wiring for the
--preview/--no-preview flag pair (issue #60) - modeled on the existing
--mirror/--no-mirror pattern."""
import argparse

import pytest

from src.config import Config
from main import apply_config_overrides, parse_args


def test_preview_flags_are_mutually_exclusive():
    with pytest.raises((SystemExit, argparse.ArgumentError)):
        parse_args(['--preview', '--no-preview', 'pose'])


def test_show_window_defaults_to_none():
    args = parse_args(['pose'])
    assert args.show_window is None


def test_preview_flag_sets_show_window_true():
    args = parse_args(['--preview', 'pose'])
    assert args.show_window is True


def test_no_preview_flag_sets_show_window_false():
    args = parse_args(['--no-preview', 'pose'])
    assert args.show_window is False


def test_apply_config_overrides_leaves_show_window_alone_by_default(tmp_path):
    config = Config(str(tmp_path / 'config.json'))
    original = config.get('display', 'show_window')
    args = parse_args(['pose'])
    apply_config_overrides(args, config)
    assert config.get('display', 'show_window') == original


def test_apply_config_overrides_applies_no_preview(tmp_path):
    config = Config(str(tmp_path / 'config.json'))
    args = parse_args(['--no-preview', 'pose'])
    apply_config_overrides(args, config)
    assert config.get('display', 'show_window') is False


def test_apply_config_overrides_applies_preview(tmp_path):
    config = Config(str(tmp_path / 'config.json'))
    config.set('display', 'show_window', False)
    args = parse_args(['--preview', 'pose'])
    apply_config_overrides(args, config)
    assert config.get('display', 'show_window') is True
