"""
LandmarkerProcessor base (#35): delegate selection and GPU -> CPU fallback,
detect_async backpressure, and that nothing of the removed Solutions path is
left importable.
"""
import os
from types import SimpleNamespace

import numpy as np
import pytest

import mediapipe as mp

import src
from src import landmarker_processor
from src.landmarker_processor import LandmarkerProcessor


class FakeLandmarker:
    """Stands in for mp.tasks.vision.*Landmarker; create_from_options records delegates"""
    created = []
    fail_on = set()

    @classmethod
    def create_from_options(cls, options):
        if options in cls.fail_on:
            raise RuntimeError("no GPU")
        cls.created.append(options)
        return SimpleNamespace(detect_async=lambda image, ts: None)


class FakeProcessor(LandmarkerProcessor):
    label = "Fake"

    def __init__(self, tmp_path, **kwargs):
        super().__init__(SimpleNamespace(), **kwargs)
        self.model = str(tmp_path / 'model.task')
        open(self.model, 'w').close()
        self.drawn = 0
        self.idle = 0

    def _download_model(self):
        return self.model

    def _landmarker_types(self):
        return FakeLandmarker, None

    def build_options(self, delegate):
        return delegate

    def _publish(self, results, target, timestamp):
        pass

    def _draw_results(self, results, target):
        self.drawn += 1

    def _report_idle(self):
        self.idle += 1


@pytest.fixture(autouse=True)
def reset_fake():
    FakeLandmarker.created = []
    FakeLandmarker.fail_on = set()


GPU = mp.tasks.BaseOptions.Delegate.GPU
CPU = mp.tasks.BaseOptions.Delegate.CPU


def test_force_cpu_never_tries_gpu(tmp_path):
    proc = FakeProcessor(tmp_path, force_cpu=True, is_apple_silicon=False)
    landmarker, backend, _, ok = proc.setup_processor()
    assert ok and backend.startswith("CPU") and not proc.use_gpu
    assert FakeLandmarker.created == [CPU]


def test_apple_silicon_defaults_to_cpu(tmp_path):
    proc = FakeProcessor(tmp_path, is_apple_silicon=True)
    assert proc.setup_processor()[3] and FakeLandmarker.created == [CPU]


def test_force_gpu_on_apple_silicon_tries_gpu(tmp_path):
    proc = FakeProcessor(tmp_path, force_gpu=True, is_apple_silicon=True)
    _, backend, _, ok = proc.setup_processor()
    assert ok and backend.startswith("GPU") and proc.use_gpu
    assert FakeLandmarker.created == [GPU]


def test_gpu_failure_falls_back_to_cpu(tmp_path):
    FakeLandmarker.fail_on = {GPU}
    proc = FakeProcessor(tmp_path, is_apple_silicon=False)
    _, backend, _, ok = proc.setup_processor()
    assert ok and backend.startswith("CPU") and not proc.use_gpu
    assert FakeLandmarker.created == [CPU]


def test_both_delegates_failing_reports_failure(tmp_path):
    FakeLandmarker.fail_on = {GPU, CPU}
    proc = FakeProcessor(tmp_path, is_apple_silicon=False)
    assert proc.setup_processor() == (None, None, None, False)


def test_missing_model_reports_failure(tmp_path):
    proc = FakeProcessor(tmp_path)
    os.remove(proc.model)
    assert proc.setup_processor() == (None, None, None, False)


def test_backpressure_skips_detect_async_and_redraws(tmp_path):
    proc = FakeProcessor(tmp_path, is_apple_silicon=False)
    calls = []
    landmarker = SimpleNamespace(detect_async=lambda image, ts: calls.append(ts))
    frame = np.zeros((480, 640, 3), np.uint8)

    proc.process_frame(frame, landmarker, 'T', 1)          # submitted, now pending
    assert calls == [1] and proc.pending_frames == 1
    proc._display_results = object()
    proc.process_frame(frame, landmarker, 'T', 2)          # backed up -> skipped
    assert calls == [1] and proc.skipped_frames == 1 and proc.drawn == 1
    proc._result_callback(object(), None, 1)               # model finished
    assert proc.pending_frames == 0
    proc.process_frame(frame, landmarker, 'T', 3)
    assert calls == [1, 3]


def test_solutions_path_is_gone():
    for name in ('LegacyPoseProcessor', 'LegacyHandProcessor', 'GPUPoseProcessor',
                 'CPUPoseProcessor', 'PoseProcessor', 'HandProcessor'):
        assert name not in src._EXPORTS
    assert 'LandmarkerProcessor' in src._EXPORTS
    assert src.LandmarkerProcessor is landmarker_processor.LandmarkerProcessor


def test_force_legacy_flag_is_gone():
    import main
    with pytest.raises(SystemExit):
        main.parse_args(['--force-legacy', 'pose'])
