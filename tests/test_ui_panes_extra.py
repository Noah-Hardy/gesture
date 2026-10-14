"""Settings panes (#68): anchors, popups on revert, and the real frame-cost measurement."""

import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ui.panes import _sources  # noqa: E402
from tests.test_ui_panes import window  # noqa: E402,F401


def test_scroll_to_anchor_scrolls_a_fresh_window(window):  # noqa: F811
    window.show_pane('advanced', 'updates.include_prereleases')
    QApplication.instance().processEvents()
    assert window.pane('advanced').verticalScrollBar().value() > 0


def test_revert_closes_an_open_popup(window):  # noqa: F811
    window.show_pane('input')
    sel = window.pane('input').bound['input.camera.device_id'].control
    sel.open_popup()
    assert sel.is_open()
    window.edits.edit('input.camera.width', '1280')
    window.edits.revert()
    assert not sel.is_open()


def _have_models():
    from src.model_downloader import get_model_path
    return all(os.path.exists(get_model_path(f'pose_landmarker_{m}.task')) for m in ('lite', 'full', 'heavy'))


def _real_measure_cost(model):
    from src import frame_cost
    ms = frame_cost.measure_model(model)
    if ms is not None:
        costs = frame_cost.load_cache()
        costs[model] = round(ms, 1)
        frame_cost.save_cache(costs)
    return ms


@pytest.mark.skipif(not _have_models(), reason='pose models not downloaded')
def test_measure_runs_end_to_end_on_a_worker_thread(window, monkeypatch, tmp_path):  # noqa: F811
    from src import frame_cost
    monkeypatch.setattr(frame_cost, 'cache_path', lambda: str(tmp_path / 'frame_cost.json'))
    monkeypatch.setattr(_sources, 'load_costs', frame_cost.load_cache)
    monkeypatch.setattr(_sources, 'measure_cost', _real_measure_cost)
    tr = window.pane('tracking')
    tr.measure_costs()
    app = QApplication.instance()
    deadline = time.time() + 90
    while tr._measuring and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert not tr._measuring
    cells = [r[2] for r in tr._cost_holder.table_rows()[1:]]
    assert all(c.endswith(' ms') for c in cells), cells
    ms = [float(c.split()[0]) for c in cells]
    assert ms[2] > ms[0]  # heavy costs more than lite, so a body was in frame
    assert set(frame_cost.load_cache()) == {'lite', 'full', 'heavy'}
