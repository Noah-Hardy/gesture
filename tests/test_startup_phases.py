"""
Startup phase reporting (#59): the report_phase seam, the model warm-up, and
the pure logic of scripts/cold_start_timing.py.
"""
import importlib.util
import os
import threading
from types import SimpleNamespace

import pytest

from src import runtime
from src.runtime import PHASES, report_phase, set_phase_handler
from tests.test_landmarker_processor import FakeProcessor


@pytest.fixture(autouse=True)
def no_handler():
    set_phase_handler(None)
    yield
    set_phase_handler(None)


def test_report_phase_logs_text_and_notifies_handler(capsys):
    seen = []
    set_phase_handler(lambda phase, reason: seen.append((phase, reason)))
    report_phase('opening_camera')
    report_phase('loading_model', message="Loading pose model (lite)…")
    report_phase('warming_up')
    report_phase('ready', reason="x")
    assert capsys.readouterr().out.splitlines() == [
        "Opening camera…", "Loading pose model (lite)…", "Warming up…", "Ready"]
    assert seen == [('opening_camera', ''), ('loading_model', ''), ('warming_up', ''), ('ready', 'x')]
    assert tuple(p for p, _ in seen) == PHASES


def test_report_phase_rejects_unknown_phase():
    with pytest.raises(ValueError):
        report_phase('exploding')


def test_failing_handler_never_stops_startup(capsys):
    def boom(phase, reason):
        raise RuntimeError("socket gone")
    set_phase_handler(boom)
    report_phase('ready')
    assert 'Phase handler failed' in capsys.readouterr().out


def test_loading_model_phase_is_reported_by_setup(tmp_path, capsys):
    seen = []
    set_phase_handler(lambda phase, reason: seen.append(phase))
    proc = FakeProcessor(tmp_path, force_cpu=True, is_apple_silicon=False)
    proc.setup_processor()
    assert seen == ['loading_model']
    assert "Loading fake model…" in capsys.readouterr().out


class AsyncLandmarker:
    """detect_async answers on another thread, like MediaPipe's worker"""

    def __init__(self, proc):
        self.proc = proc
        self.calls = []

    def detect_async(self, image, ts):
        self.calls.append(ts)
        threading.Timer(0.02, self.proc._result_callback, args=(SimpleNamespace(), None, ts)).start()


def test_warm_up_runs_one_detection_and_swallows_its_result(tmp_path):
    proc = FakeProcessor(tmp_path, is_apple_silicon=False)
    landmarker = AsyncLandmarker(proc)
    assert proc.warm_up(landmarker) is True
    assert landmarker.calls == [0]          # one call, below the loop's first timestamp (1)
    assert proc.pending_frames == 0
    assert proc.results is None and not proc._has_fresh_results   # never published


def test_warm_up_times_out_without_blocking_forever(tmp_path):
    proc = FakeProcessor(tmp_path, is_apple_silicon=False)
    assert proc.warm_up(SimpleNamespace(detect_async=lambda image, ts: None), timeout=0.05) is False


def test_results_after_warm_up_are_taken_normally(tmp_path):
    proc = FakeProcessor(tmp_path, is_apple_silicon=False)
    proc.warm_up(AsyncLandmarker(proc))
    proc._result_callback('real', None, 1)
    assert proc.results == 'real' and proc._has_fresh_results


def test_runtime_exports_the_documented_phase_ids():
    assert PHASES == ('opening_camera', 'loading_model', 'warming_up', 'ready')
    assert runtime.report_phase is report_phase


# ----------------------------------------------------------------------------
# scripts/cold_start_timing.py
# ----------------------------------------------------------------------------
def _load_script():
    path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'cold_start_timing.py')
    spec = importlib.util.spec_from_file_location('cold_start_timing', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cold = _load_script()


@pytest.mark.parametrize("measured,ok", [(4.0, True), (5.0, True), (5.01, False), (9.0, False)])
def test_evaluate_allows_25_percent_over_baseline(measured, ok):
    result, limit = cold.evaluate(measured, 4.0)
    assert result is ok and limit == pytest.approx(5.0)


def test_baseline_round_trip_keeps_other_modes(tmp_path):
    path = str(tmp_path / 'baseline.json')
    assert cold.load_baselines(path) == {}
    cold.save_baseline('pose', 3.21456, {'ready': 3.21456}, path)
    cold.save_baseline('all', 4.5, {}, path)
    data = cold.load_baselines(path)
    assert data['pose']['seconds'] == 3.215 and data['all']['seconds'] == 4.5


def test_phase_lines_are_recognised():
    assert cold.phase_for_line("Opening camera…\n") == 'opening_camera'
    assert cold.phase_for_line("Connecting to NDI source…") == 'opening_camera'
    assert cold.phase_for_line("Loading pose model (lite)…") == 'loading_model'
    assert cold.phase_for_line("Warming up…") == 'warming_up'
    assert cold.phase_for_line("Ready") == 'ready'
    assert cold.phase_for_line("\U0001f7e2 Engine ready") is None
