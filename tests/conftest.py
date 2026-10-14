"""Shared pytest setup: keep the MainWindow's launch-time update check off the network."""

import pytest


@pytest.fixture(autouse=True)
def _no_launch_update_check(monkeypatch):
    try:
        from src.ui.overlays import update
    except Exception:  # PySide6 not installed
        return
    monkeypatch.setattr(update, 'LAUNCH_CHECK_DELAY_MS', 10 ** 9)
