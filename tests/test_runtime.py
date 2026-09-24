"""
src.runtime: the engine-lifetime and capture-loop helpers main.py's two
processing loops share - parent-liveness watch and SIGTERM handling (#34).
"""
import os
import signal

import pytest

from src import runtime
from src.runtime import ParentWatch, install_sigterm_handler


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


# ----------------------------------------------------------------------------
# ParentWatch
# ----------------------------------------------------------------------------

def test_parent_watch_disabled_never_fires(monkeypatch):
    watch = ParentWatch(enabled=False)
    monkeypatch.setattr(runtime.os, 'getppid', lambda: 1)
    assert watch.parent_pid is None
    assert watch.parent_gone() is False


def test_parent_watch_fires_when_reparented(monkeypatch):
    monkeypatch.setattr(runtime.os, 'getppid', lambda: 4242)
    clock = FakeClock()
    watch = ParentWatch(enabled=True, clock=clock)
    assert watch.parent_gone() is False

    # Launcher force-quit: the kernel reparents us to launchd
    monkeypatch.setattr(runtime.os, 'getppid', lambda: 1)
    clock.now += ParentWatch.CHECK_INTERVAL
    assert watch.parent_gone() is True


def test_parent_watch_checks_about_once_a_second(monkeypatch):
    calls = []

    def fake_getppid():
        calls.append(1)
        return 4242

    monkeypatch.setattr(runtime.os, 'getppid', fake_getppid)
    clock = FakeClock()
    watch = ParentWatch(enabled=True, clock=clock)
    calls.clear()
    for _ in range(50):
        watch.parent_gone()
        clock.now += 0.01   # 50 frames in half a second
    assert len(calls) == 1


# ----------------------------------------------------------------------------
# SIGTERM
# ----------------------------------------------------------------------------

def test_sigterm_raises_keyboard_interrupt():
    previous = signal.getsignal(signal.SIGTERM)
    try:
        install_sigterm_handler()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
            # The handler runs between bytecodes; give it one to land on
            for _ in range(1000):
                pass
    finally:
        signal.signal(signal.SIGTERM, previous)
