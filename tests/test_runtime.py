"""
src.runtime: the engine-lifetime and capture-loop helpers main.py's two
processing loops share - parent-liveness watch and SIGTERM handling (#34),
capture reconnect with backoff (#32) and deadline frame pacing (#36).
"""
import os
import signal

import pytest

from src import runtime
from src.runtime import FrameClock, ParentWatch, ReconnectingCapture, install_sigterm_handler


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


# ----------------------------------------------------------------------------
# ReconnectingCapture (#32)
# ----------------------------------------------------------------------------

class FakeCap:
    """Scripted capture: each read() pops the next bool from `script`"""

    def __init__(self, script=(), opened=True):
        self.script = list(script)
        self.opened = opened
        self.released = False
        self.reads = 0

    def isOpened(self):
        return self.opened and not self.released

    def read(self):
        self.reads += 1
        ok = self.script.pop(0) if self.script else False
        return (True, 'frame') if ok else (False, None)

    def release(self):
        self.released = True


class SleepRecorder:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.now += seconds


def make_capture(cap, reopen=None, timeout=30):
    clock = FakeClock()
    sleep = SleepRecorder(clock)
    wrapper = ReconnectingCapture(cap, reopen or (lambda old: old), timeout, clock=clock, sleep=sleep)
    return wrapper, clock, sleep


def drain(wrapper, iterations):
    """Run the loop's read pattern `iterations` times; return successes"""
    frames = 0
    for _ in range(iterations):
        ret, _ = wrapper.read()
        if ret:
            frames += 1
        elif wrapper.gave_up:
            break
    return frames


def test_successful_reads_pass_straight_through():
    cap = FakeCap([True, True, True])
    wrapper, _, sleep = make_capture(cap)
    assert drain(wrapper, 3) == 3
    assert sleep.calls == []


def test_failures_back_off_instead_of_spinning():
    cap = FakeCap([])  # never delivers
    wrapper, clock, sleep = make_capture(cap, timeout=0)
    start = clock.now
    drain(wrapper, 200)
    # A busy spin would have made 200 reads in zero time
    assert cap.reads < 200
    assert clock.now - start > 10
    assert max(sleep.calls) <= ReconnectingCapture.IDLE_SLICE


def test_backoff_grows_to_the_ceiling():
    wrapper, _, _ = make_capture(FakeCap([]), timeout=0)
    drain(wrapper, 500)
    assert wrapper._backoff == ReconnectingCapture.MAX_BACKOFF


def test_loop_keeps_iterating_during_backoff():
    # Every read() during a 2s backoff returns within one IDLE_SLICE, so a
    # heartbeat at the loop top still fires at >= 1 Hz
    wrapper, clock, sleep = make_capture(FakeCap([]), timeout=0)
    drain(wrapper, 300)
    assert all(s <= ReconnectingCapture.IDLE_SLICE for s in sleep.calls)


def test_reopens_every_k_failures():
    reopened = []

    def reopen(old):
        old.release()
        new = FakeCap([])
        reopened.append(new)
        return new

    wrapper, _, _ = make_capture(FakeCap([]), reopen=reopen, timeout=0)
    while len(reopened) < 2:
        wrapper.read()
    assert wrapper.failures == 2 * ReconnectingCapture.REOPEN_AFTER
    assert wrapper.cap is reopened[-1]


def test_recovers_after_reopen_and_resets_backoff():
    fresh = FakeCap([True, True])

    def reopen(old):
        old.release()
        return fresh

    wrapper, _, _ = make_capture(FakeCap([]), reopen=reopen)
    for _ in range(1000):
        ret, _ = wrapper.read()
        if ret:
            break
    assert ret is True
    assert wrapper.failures == 0
    assert wrapper._backoff == ReconnectingCapture.MIN_BACKOFF
    assert wrapper.gave_up is False


def test_reopen_returning_none_keeps_retrying():
    wrapper, _, _ = make_capture(FakeCap([]), reopen=lambda old: None, timeout=0)
    drain(wrapper, 100)
    assert wrapper.cap is None
    assert wrapper.gave_up is False
    assert wrapper.isOpened() is False


def test_reopen_raising_is_survived():
    def reopen(old):
        raise RuntimeError('camera busy')

    wrapper, _, _ = make_capture(FakeCap([]), reopen=reopen, timeout=0)
    drain(wrapper, 100)
    assert wrapper.gave_up is False


def test_gives_up_on_wall_clock_deadline():
    wrapper, clock, _ = make_capture(FakeCap([]), timeout=10)
    start = clock.now
    drain(wrapper, 10_000)
    assert wrapper.gave_up is True
    assert 10 <= clock.now - start < 13


def test_release_releases_the_current_capture():
    fresh = FakeCap([])
    wrapper, _, _ = make_capture(FakeCap([]), reopen=lambda old: fresh, timeout=0)
    drain(wrapper, 50)
    wrapper.release()
    assert fresh.released is True


# ----------------------------------------------------------------------------
# FrameClock (#36)
# ----------------------------------------------------------------------------

def test_frame_clock_uncapped_never_sleeps():
    clock = FakeClock()
    sleep = SleepRecorder(clock)
    FrameClock(0, clock=clock, sleep=sleep).wait()
    assert sleep.calls == []


def test_frame_clock_absorbs_per_frame_work():
    # 10ms of work per frame at a 30 FPS cap: the old sleep-then-rebaseline
    # cap added the work on top of every interval; the deadline cap doesn't
    clock = FakeClock()
    sleep = SleepRecorder(clock)
    frame_clock = FrameClock(1 / 30, clock=clock, sleep=sleep)
    start = clock.now
    for _ in range(30):
        frame_clock.wait()
        clock.now += 0.010
    assert abs((clock.now - start) - 1.0) < 0.05


def test_frame_clock_resyncs_after_a_stall():
    clock = FakeClock()
    sleep = SleepRecorder(clock)
    frame_clock = FrameClock(0.1, clock=clock, sleep=sleep)
    frame_clock.wait()
    clock.now += 5.0  # a long stall (reconnect, model hiccup)
    frame_clock.wait()
    sleep.calls.clear()
    frame_clock.wait()
    # Next frame waits a normal interval rather than bursting through the
    # 50 missed slots with no sleep at all
    assert sleep.calls and abs(sleep.calls[0] - 0.1) < 1e-9
