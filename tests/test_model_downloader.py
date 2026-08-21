"""
_download_model: the atomic, size-verified download path shared by
download_pose_model/download_hand_model/download_holistic_model. urlopen is
monkeypatched throughout - no real network traffic, no touching this repo's
actual bundled src/tasks/ models.
"""
import io
import os

import pytest

import src.model_downloader as md


class FakeHTTPResponse(io.BytesIO):
    """Minimal stand-in for the urlopen() context manager"""
    def __init__(self, data, content_length=None):
        super().__init__(data)
        self.headers = {}
        if content_length is not None:
            self.headers['Content-Length'] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def isolated_dirs(monkeypatch, tmp_path):
    # Point both the "bundled" and "writable" model directories at empty
    # tmp dirs so tests never see (or risk touching) this repo's real,
    # already-downloaded models under src/tasks/.
    monkeypatch.setattr(md, '_bundled_tasks_dir', lambda: str(tmp_path / 'bundled'))
    monkeypatch.setattr(md, 'TASKS_DIR', str(tmp_path / 'writable'))
    return tmp_path


def test_successful_download_lands_at_final_path_and_is_returned(monkeypatch, isolated_dirs):
    body = b'x' * 100
    monkeypatch.setattr(md.urllib.request, 'urlopen',
                         lambda url: FakeHTTPResponse(body, content_length=len(body)))
    path = md.download_hand_model()
    assert path == os.path.join(str(isolated_dirs / 'writable'), 'hand_landmarker.task')
    assert os.path.exists(path)
    with open(path, 'rb') as f:
        assert f.read() == body


def test_truncated_download_returns_none_and_leaves_no_files(monkeypatch, isolated_dirs):
    # Server claimed 1000 bytes but the body only has 100 - simulates an
    # interrupted download. This is the actual regression #28 fixes: the
    # old urlretrieve-straight-to-final-path left a truncated .task file
    # that looked valid to os.path.exists forever after.
    body = b'x' * 100
    monkeypatch.setattr(md.urllib.request, 'urlopen',
                         lambda url: FakeHTTPResponse(body, content_length=1000))
    result = md.download_hand_model()
    assert result is None

    final_path = os.path.join(str(isolated_dirs / 'writable'), 'hand_landmarker.task')
    assert not os.path.exists(final_path)
    leftovers = os.listdir(str(isolated_dirs / 'writable'))
    assert leftovers == [], f"expected no leftover files, found {leftovers}"


def test_missing_content_length_still_succeeds(monkeypatch, isolated_dirs):
    body = b'model bytes'
    monkeypatch.setattr(md.urllib.request, 'urlopen',
                         lambda url: FakeHTTPResponse(body, content_length=None))
    path = md.download_hand_model()
    assert path is not None
    assert os.path.exists(path)


def test_existing_model_short_circuits_without_network_call(monkeypatch, isolated_dirs):
    writable = isolated_dirs / 'writable'
    writable.mkdir(parents=True)
    existing = writable / 'hand_landmarker.task'
    existing.write_bytes(b'already here')

    def boom(url):
        raise AssertionError("urlopen should not be called when a model already exists")
    monkeypatch.setattr(md.urllib.request, 'urlopen', boom)

    path = md.download_hand_model()
    assert path == str(existing)


def test_bundled_model_is_preferred_over_writable_and_skips_network(monkeypatch, isolated_dirs):
    bundled = isolated_dirs / 'bundled'
    bundled.mkdir(parents=True)
    (bundled / 'hand_landmarker.task').write_bytes(b'bundled copy')

    def boom(url):
        raise AssertionError("urlopen should not be called when a bundled model exists")
    monkeypatch.setattr(md.urllib.request, 'urlopen', boom)

    path = md.download_hand_model()
    assert path == str(bundled / 'hand_landmarker.task')


def test_invalid_pose_model_type_falls_back_to_lite(monkeypatch, isolated_dirs):
    writable = isolated_dirs / 'writable'
    writable.mkdir(parents=True)
    existing = writable / 'pose_landmarker_lite.task'
    existing.write_bytes(b'lite model')

    def boom(url):
        raise AssertionError("should have used the existing lite model, not hit the network")
    monkeypatch.setattr(md.urllib.request, 'urlopen', boom)

    path = md.download_pose_model('not-a-real-type')
    assert path == str(existing)


def test_download_failure_is_not_cached_as_valid(monkeypatch, isolated_dirs):
    # A failed download must not leave anything a later os.path.exists
    # check would mistake for a real model.
    def raise_error(url):
        raise OSError("network unreachable")
    monkeypatch.setattr(md.urllib.request, 'urlopen', raise_error)

    first = md.download_hand_model()
    assert first is None
    final_path = os.path.join(str(isolated_dirs / 'writable'), 'hand_landmarker.task')
    assert not os.path.exists(final_path)

    # A second, successful attempt must still be treated as "not present yet"
    body = b'now it works'
    monkeypatch.setattr(md.urllib.request, 'urlopen',
                         lambda url: FakeHTTPResponse(body, content_length=len(body)))
    second = md.download_hand_model()
    assert second == final_path
    assert os.path.exists(final_path)
