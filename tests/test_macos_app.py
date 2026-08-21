"""
macos_app: reassert_accessory_policy() must keep working indefinitely, not
give up after a fixed number of calls - that budget-then-stop behavior was
the root cause of #43 (the second Dock icon sticking around once HighGUI's
policy reset landed later than the old 10-call budget allowed for).

ctypes/objc plumbing is monkeypatched throughout - no real objc calls, and
no dependency on actually running on macOS.
"""
import src.macos_app as macos_app


def test_off_darwin_is_a_safe_no_op(monkeypatch):
    monkeypatch.setattr(macos_app, '_state', None)
    monkeypatch.setattr(macos_app.platform, 'system', lambda: 'Linux')
    assert macos_app.set_accessory_policy() is False
    assert macos_app.reassert_accessory_policy() is False
    # Cached as a permanent "unavailable" rather than re-checked every call
    assert macos_app._state is False


def test_reassert_keeps_succeeding_well_past_the_old_ten_call_budget(monkeypatch):
    monkeypatch.setattr(macos_app, '_state', None)
    monkeypatch.setattr(macos_app.platform, 'system', lambda: 'Darwin')

    calls = {'count': 0}

    def fake_send_policy(app, sel, policy):
        calls['count'] += 1
        return True

    monkeypatch.setattr(macos_app, '_objc_state',
                         lambda: (object(), fake_send_policy, object()))

    # The old implementation capped at _MAX_REASSERT_CALLS = 10 and then
    # went permanently silent. 50 calls here would have failed under that
    # behavior; every one of them must succeed now.
    results = [macos_app.reassert_accessory_policy() for _ in range(50)]
    assert results == [True] * 50
    assert calls['count'] == 50


def test_objc_state_is_cached_across_repeated_calls(monkeypatch):
    monkeypatch.setattr(macos_app, '_state', None)
    monkeypatch.setattr(macos_app.platform, 'system', lambda: 'Darwin')

    load_calls = {'count': 0}
    real_load_library = macos_app.ctypes.cdll.LoadLibrary

    def fake_load_library(*a, **k):
        load_calls['count'] += 1
        raise OSError("objc unavailable in this test")

    monkeypatch.setattr(macos_app.ctypes.cdll, 'LoadLibrary', fake_load_library)

    # First call attempts the load and fails; result is cached as False so
    # a second call must not retry ctypes.cdll.LoadLibrary at all.
    assert macos_app._objc_state() is None
    assert macos_app._objc_state() is None
    assert load_calls['count'] == 1
    monkeypatch.setattr(macos_app.ctypes.cdll, 'LoadLibrary', real_load_library)


def test_apply_accessory_policy_survives_a_call_that_raises(monkeypatch):
    monkeypatch.setattr(macos_app, '_state', None)
    monkeypatch.setattr(macos_app.platform, 'system', lambda: 'Darwin')

    def boom(app, sel, policy):
        raise OSError("simulated objc failure")

    monkeypatch.setattr(macos_app, '_objc_state', lambda: (object(), boom, object()))
    assert macos_app.set_accessory_policy() is False
    assert macos_app.reassert_accessory_policy() is False
