"""
Issue #81: the launcher reopens in the state it was last used in. Drives
LauncherGui's form<->config methods against a stub, so no Tk root is needed.
"""
from types import SimpleNamespace

from src.config import Config
from src.gui import LauncherGui


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _stub(tmp_path, **overrides):
    form = dict(mode='hand', source='camera', camera='2', ndi_source='', host='10.0.0.5',
                port='9000', pose_model='full', fps_cap='30', show_fps=True, show_preview=False,
                mirror=True, force_cpu=True, force_gpu=False, force_legacy=False, no_holistic=True)
    form.update(overrides)
    gui = SimpleNamespace(config=Config(str(tmp_path / 'config.json')), _int_or_none=None)
    for key, value in form.items():
        setattr(gui, 'var_' + key, Var(value))
    gui._int_or_none = lambda raw: LauncherGui._int_or_none(gui, raw)
    gui._apply_form_to_config = lambda: LauncherGui._apply_form_to_config(gui)
    return gui


def test_last_used_form_round_trips_through_a_relaunch(tmp_path):
    gui = _stub(tmp_path)
    LauncherGui._persist_last_used(gui)

    reopened = _stub(tmp_path, mode='all', camera='0', host='', port='1', pose_model='lite')
    reopened.config = Config(str(tmp_path / 'config.json'))
    LauncherGui._seed_vars_from_config(reopened)

    assert reopened.var_mode.get() == 'hand'
    assert reopened.var_camera.get() == '2'
    assert reopened.var_host.get() == '10.0.0.5'
    assert reopened.var_port.get() == '9000'
    assert reopened.var_pose_model.get() == 'full'
    assert reopened.var_fps_cap.get() == '30'
    assert reopened.var_mirror.get() is True
    assert reopened.var_show_preview.get() is False
    assert reopened.var_force_cpu.get() is True
    assert reopened.var_no_holistic.get() is True


def test_auto_save_keeps_previous_value_for_invalid_fields(tmp_path):
    LauncherGui._persist_last_used(_stub(tmp_path))
    bad = _stub(tmp_path, port='99999', camera='abc', host='  ', mode='hand')
    bad.config = Config(str(tmp_path / 'config.json'))
    LauncherGui._persist_last_used(bad)

    saved = Config(str(tmp_path / 'config.json'))
    assert saved.get('osc', 'port') == 9000
    assert saved.get('camera', 'device_id') == 2
    assert saved.get('osc', 'host') == '10.0.0.5'


def test_unknown_saved_mode_falls_back_to_all(tmp_path):
    gui = _stub(tmp_path)
    gui.config.set('ui', 'mode', 'bogus')
    LauncherGui._seed_vars_from_config(gui)
    assert gui.var_mode.get() == 'all'
