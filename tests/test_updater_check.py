"""
check_for_update: the ETag/304, throttle and rate-limit matrix, with
urlopen mocked so no real network call happens.

`config` is duck-typed here as anything with a `.get(section, default=None)`
method returning a dict, matching src.config.Config.get(section) - see
check_for_update's `updates_cfg = config.get('updates') or {}`.
"""
import io
import json
import urllib.error

import pytest

from src.updater import check_for_update


class FakeConfig:
    def __init__(self, updates=None):
        self._data = {'updates': updates or {}}

    def get(self, section, key=None, default=None):
        if key is None:
            return self._data.get(section, default)
        return self._data.get(section, {}).get(key, default)


class FakeResponse:
    """Minimal stand-in for the urlopen() context manager"""
    def __init__(self, body, headers=None):
        self._body = json.dumps(body).encode('utf-8')
        self.headers = headers or {}

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _release_payload(tag='v0.9.9', prerelease=False):
    name = f'MP-OSC-{tag.lstrip("v")}-macos-arm64.zip'
    return {
        'tag_name': tag, 'name': tag, 'body': '', 'html_url': 'https://example',
        'prerelease': prerelease, 'draft': False,
        'assets': [
            {'name': name, 'browser_download_url': f'https://example/{name}', 'size': 1},
            {'name': name + '.sha256', 'browser_download_url': f'https://example/{name}.sha256'},
        ],
    }


@pytest.fixture(autouse=True)
def fake_current_version(monkeypatch):
    # current_version() reads docs.app_version() by default; pin it so
    # every test compares against a known baseline regardless of the
    # checked-out pyproject.toml version.
    monkeypatch.setenv('MPOSC_UPDATE_FAKE_VERSION', '0.1.0')


def test_returns_available_for_a_newer_release(monkeypatch):
    monkeypatch.setattr('src.updater.urllib.request.urlopen',
                         lambda *a, **k: FakeResponse([_release_payload('v0.9.9')]))
    result = check_for_update(FakeConfig(), manual=True)
    assert result.kind == 'available'
    assert result.release.version == '0.9.9'


def test_returns_none_when_current_is_newest(monkeypatch):
    monkeypatch.setattr('src.updater.urllib.request.urlopen',
                         lambda *a, **k: FakeResponse([_release_payload('v0.0.1')]))
    result = check_for_update(FakeConfig(), manual=True)
    assert result.kind == 'none'


def test_prerelease_not_offered_when_config_omits_the_key(monkeypatch):
    # updates_cfg.get('include_prereleases', False) - the fallback itself
    # must default to False, not just the config.py schema default, for a
    # config dict that omits the key entirely.
    monkeypatch.setattr('src.updater.urllib.request.urlopen',
                         lambda *a, **k: FakeResponse([_release_payload('v0.9.9', prerelease=True)]))
    result = check_for_update(FakeConfig({}), manual=True)
    assert result.kind == 'none'


def test_304_not_modified_returns_none_and_persists_last_check(monkeypatch):
    def raise_304(*a, **k):
        raise urllib.error.HTTPError('url', 304, 'Not Modified', {}, io.BytesIO())
    monkeypatch.setattr('src.updater.urllib.request.urlopen', raise_304)
    result = check_for_update(FakeConfig({'last_etag': 'abc'}), manual=False)
    assert result.kind == 'none'
    assert 'last_check' in result.persist


def test_403_sets_rate_limited_until_and_errors_on_manual_check(monkeypatch):
    def raise_403(*a, **k):
        raise urllib.error.HTTPError('url', 403, 'Forbidden', {'x-ratelimit-reset': '9999999999'}, io.BytesIO())
    monkeypatch.setattr('src.updater.urllib.request.urlopen', raise_403)
    result = check_for_update(FakeConfig(), manual=True)
    assert result.kind == 'error'
    assert result.persist['rate_limited_until'] == 9999999999


def test_403_on_launch_check_is_silent(monkeypatch):
    def raise_403(*a, **k):
        raise urllib.error.HTTPError('url', 403, 'Forbidden', {}, io.BytesIO())
    monkeypatch.setattr('src.updater.urllib.request.urlopen', raise_403)
    result = check_for_update(FakeConfig(), manual=False)
    assert result.kind == 'none'
    assert 'rate_limited_until' in result.persist


def test_rate_limit_suppresses_manual_check_before_it_expires(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("urlopen should not be called while rate-limited")
    monkeypatch.setattr('src.updater.urllib.request.urlopen', boom)
    import time
    result = check_for_update(FakeConfig({'rate_limited_until': time.time() + 3600}), manual=True)
    assert result.kind == 'error'


def test_launch_check_disabled_returns_none_without_network_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("urlopen should not be called when check_on_launch is False")
    monkeypatch.setattr('src.updater.urllib.request.urlopen', boom)
    result = check_for_update(FakeConfig({'check_on_launch': False}), manual=False)
    assert result.kind == 'none'


def test_skipped_version_is_not_reoffered_on_launch_check(monkeypatch):
    monkeypatch.setattr('src.updater.urllib.request.urlopen',
                         lambda *a, **k: FakeResponse([_release_payload('v0.9.9')]))
    result = check_for_update(FakeConfig({'skipped_version': 'v0.9.9'}), manual=False)
    assert result.kind == 'none'


def test_skipped_version_is_still_offered_on_manual_check(monkeypatch):
    monkeypatch.setattr('src.updater.urllib.request.urlopen',
                         lambda *a, **k: FakeResponse([_release_payload('v0.9.9')]))
    result = check_for_update(FakeConfig({'skipped_version': 'v0.9.9'}), manual=True)
    assert result.kind == 'available'


def test_missing_current_version_errors(monkeypatch):
    monkeypatch.setenv('MPOSC_UPDATE_FAKE_VERSION', '')
    monkeypatch.setattr('src.updater.docs.app_version', lambda: '')
    result = check_for_update(FakeConfig(), manual=True)
    assert result.kind == 'error'


def test_network_error_is_silent_on_launch_check(monkeypatch):
    def raise_urlerror(*a, **k):
        raise urllib.error.URLError('no route to host')
    monkeypatch.setattr('src.updater.urllib.request.urlopen', raise_urlerror)
    result = check_for_update(FakeConfig(), manual=False)
    assert result.kind == 'none'


def test_malformed_json_response_is_an_error_on_manual_check(monkeypatch):
    class BadResponse(FakeResponse):
        def read(self, n=-1):
            return b'not json'
    monkeypatch.setattr('src.updater.urllib.request.urlopen', lambda *a, **k: BadResponse([]))
    result = check_for_update(FakeConfig(), manual=True)
    assert result.kind == 'error'
