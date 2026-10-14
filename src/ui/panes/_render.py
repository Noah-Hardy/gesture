#!/usr/bin/env python3
"""
Settings Pane Renderer

Turns a PaneSpec (spec.py) into a scrolling page of FieldRows bound to the
window's EditModel: control <-> field id <-> config key. A control edit goes to
`edits.edit()`; the model's `changed` / `reverted` / `applied` signals write the
pending value back into the control, set the dirty marker and show the error.
Nothing here writes to Config.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QColorDialog, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from . import _sources as sources
from .spec import PaneSpec, Row
from ..tokens import Font, color
from ..widgets import (
    Button, CodeBlock, ColorSwatch, FieldRow, PathRow, RangeSlider, SegmentedChoice, Select,
    SpecTable, StatusDot, TextField, Toggle,
)

MAX_WIDTH = 720
NDI_REFRESH_MS = 8000
CAMERA_REFRESH_MS = 5000
MODELS = ('lite', 'full', 'heavy')
MODEL_BEST_FOR = {
    'lite': 'full-body dance, whole-room framing',
    'full': 'seated or close framing, finer joints',
    'heavy': 'offline capture, reference recordings',
}
ADDRESS_COLS = (186, 0, 66)
COST_COLS = (62, 0, 96)

_NO_NDI = 'No sources found'
_ANY_NDI = 'Any (first found)'


# ============================================================================
# CODECS (config value <-> control value)
# ============================================================================
def bgr_to_hex(bgr) -> str:
    """Stored BGR list -> '#RRGGBB'."""
    try:
        b, g, r = (max(0, min(255, int(c))) for c in bgr)
    except (TypeError, ValueError):
        return '#000000'
    return '#{:02X}{:02X}{:02X}'.format(r, g, b)


def hex_to_bgr(text: str) -> list:
    """'#RRGGBB' -> BGR list for config.json."""
    c = QColor(text)
    return [c.blue(), c.green(), c.red()]


#: name -> (decode: config -> control form, encode: control form -> config)
CODECS = {
    'source': (lambda v: 'NDI' if v else 'Camera', lambda v: v == 'NDI'),
    'bgr': (bgr_to_hex, hex_to_bgr),
}


# ============================================================================
# BACKGROUND WORK
# ============================================================================
class Async(QObject):
    """Runs a blocking function on a daemon thread and delivers its result on the GUI thread."""

    _result = Signal(object, object)

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self._result.connect(self._deliver)

    def run(self, fn: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        """Call `fn()` off-thread, then `callback(value)` (None if it raised) on the GUI thread."""
        def work():
            try:
                value = fn()
            except Exception:
                value = None
            try:
                self._result.emit(callback, value)
            except RuntimeError:  # the pane was deleted meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _deliver(callback, value) -> None:
        callback(value)


# ============================================================================
# SMALL WIDGETS
# ============================================================================
class _Heading(QLabel):
    def __init__(self, text: str):
        super().__init__(text.upper())
        f = Font.ui(11)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.5)
        self.setFont(f)
        self.setStyleSheet(
            f"color: {color('accent').name()}; background: transparent; padding: 4px 0 8px 0;")


class _Divider(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(1 + 2 * 18)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0, color('border_2'))
        end = QColor(color('border_2'))
        end.setAlpha(0)
        g.setColorAt(1, end)
        p.fillRect(0, 18, self.width(), 1, g)


class _Holder(QWidget):
    """Expanding slot whose single child can be swapped (tables rebuilt on data change)."""

    def __init__(self):
        super().__init__()
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(4, 4, 0, 4)
        self._lay.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._table: Optional[SpecTable] = None

    def set_rows(self, rows: list, cols: tuple) -> None:
        if self._table is not None:
            self._lay.removeWidget(self._table)
            self._table.setParent(None)
            self._table.deleteLater()
        self._table = SpecTable(rows, cols)
        self._lay.insertWidget(0, self._table)

    def table_rows(self) -> list:
        return list(self._table._rows) if self._table is not None else []

    def add_extra(self, widget: QWidget) -> None:
        self._lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)


class _ColorControl(QWidget):
    """Swatch with hex readout and a Choose... button (QColorDialog)."""

    colorChanged = Signal(str)

    def __init__(self, hex_color: str):
        super().__init__()
        self.swatch = ColorSwatch(hex_color)
        self.button = Button('Choose…')
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(self.swatch)
        lay.addWidget(self.button)
        lay.addStretch(1)
        self.button.clicked.connect(self._choose)
        self.setFocusProxy(self.button)

    def hex(self) -> str:
        return self.swatch.colorHex()

    def set_hex(self, hex_color: str) -> None:
        self.swatch.setColor(hex_color)

    def _choose(self) -> None:
        picked = QColorDialog.getColor(QColor(self.hex()), self.window(), 'Choose a colour',
                                       QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if picked.isValid():
            self.colorChanged.emit(picked.name().upper())


# ============================================================================
# BINDING
# ============================================================================
class Bound:
    """One editable row: field id <-> FieldRow <-> control."""

    def __init__(self, fid: str, row: Row, field_row: FieldRow, control: QWidget):
        self.fid, self.row, self.field_row, self.control = fid, row, field_row, control
        self.values: list = []   # select: option values, parallel to labels
        self.labels: list = []

    # -- control -> value ------------------------------------------------
    def read(self) -> Any:
        k, c = self.row.kind, self.control
        if k == 'toggle':
            return c.isChecked()
        if k == 'choice':
            return self.values[self.labels.index(c.current())] if self.values else c.current()
        if k == 'select':
            label = c.currentText()
            return self.values[self.labels.index(label)] if label in self.labels else None
        if k in ('num', 'decimal', 'text'):
            return c.text()
        if k == 'range':
            return round(c.value(), 2)
        if k == 'color':
            return c.hex()
        raise ValueError(k)

    # -- value -> control (callers set the pane's _syncing guard) -----------
    def write(self, value: Any) -> None:
        k, c = self.row.kind, self.control
        if k == 'toggle':
            c.setChecked(bool(value))
        elif k == 'choice':
            c.setCurrent(self.labels[self.values.index(value)] if self.values else value)
        elif k == 'select':
            if value in self.values:
                c.setCurrentText(self.labels[self.values.index(value)])
        elif k in ('num', 'decimal', 'text'):
            if c.text() != str(value):
                c.setText(str(value))
        elif k == 'range':
            if abs(c.value() - float(value)) > 1e-9:
                c.setValue(float(value))
        elif k == 'color':
            c.set_hex(value)


# ============================================================================
# PANE
# ============================================================================
class SettingsPane(QScrollArea):
    """
    A settings page built from a PaneSpec.

    Args:
        window: The MainWindow (config, edits, engine, reveal_config()).
        spec: The pane's rows.
    """

    def __init__(self, window, spec: PaneSpec):
        super().__init__()
        self.window_ref = window
        self.spec = spec
        self.edits = window.edits
        self.config = window.config
        self.bound: dict[str, Bound] = {}        # fid -> Bound
        self._rows: dict[str, QWidget] = {}      # config path -> row widget (anchors)
        self._async = Async(self)
        self._camera_names: Optional[list] = None
        self._camera_auth = 'unknown'
        self._ndi_sources: Optional[list] = None
        self._ndi_busy = False
        self._camera_busy = False
        self._costs: dict = {}
        self._measuring = False
        self._map_holder: Optional[_Holder] = None
        self._sample_row: Optional[FieldRow] = None
        self._sample_block: Optional[CodeBlock] = None
        self._permission: Optional[StatusDot] = None
        self._cost_holder: Optional[_Holder] = None
        self._measure_button: Optional[Button] = None
        self._syncing = False

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setObjectName(f'{spec.key}Pane')
        self.viewport().setAutoFillBackground(False)

        page = QWidget()
        page.setStyleSheet('background: transparent;')
        outer = QVBoxLayout(page)
        outer.setContentsMargins(32, 26, 32, 26)
        column = QWidget()
        column.setMaximumWidth(MAX_WIDTH)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        outer.addWidget(column, 0, Qt.AlignmentFlag.AlignTop)
        outer.addStretch(1)

        title = QLabel(spec.title)
        title.setFont(Font.ui(19, 500))
        title.setStyleSheet('background: transparent; font-size: 19px; font-weight: 500;')
        blurb = QLabel(spec.blurb)
        blurb.setFont(Font.ui(13))
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {color('dim').name()}; background: transparent;")
        col.addWidget(title)
        col.addSpacing(6)
        col.addWidget(blurb)
        col.addSpacing(26)

        for row in spec.rows:
            col.addWidget(self._build_row(row))
        self.setWidget(page)

        self.edits.changed.connect(self._on_changed)
        self.edits.reverted.connect(self.sync_all)
        self.edits.applied.connect(lambda _fids: self.sync_all())
        self.sync_all()

    # -- construction -----------------------------------------------------
    def _build_row(self, row: Row) -> QWidget:
        if row.kind == 'head':
            return _Heading(row.label)
        if row.kind == 'sep':
            return _Divider()
        if row.key:
            return self._build_editable(row)
        return self._build_display(row)

    def _register(self, row: Row) -> str:
        section_key = row.key
        dec, enc = CODECS.get(row.codec, (None, None))
        kind = row.kind
        default = row.default

        def decode(v, _dec=dec, _kind=kind, _default=default):
            if v is None:
                v = _default
            if _dec is not None:
                v = _dec(v)
            if _kind in ('num', 'decimal'):
                return str(v)
            if _kind == 'toggle':
                return bool(v)
            return v

        kw: dict = {}
        if enc is not None:
            kw['encode'] = enc
        if kind == 'text' and row.wide:
            kw['host'] = True
        return self.edits.register(self.spec.key, self.spec.title, row.bar_label or row.label,
                                   section_key, kind=kind, restart=row.restart, limits=row.limits,
                                   decode=decode, **kw)

    def _control_for(self, row: Row, fid: str) -> tuple[QWidget, Bound]:
        value = self.edits.saved(fid)
        k = row.kind
        values: list = []
        labels: list = []
        if k == 'toggle':
            control: QWidget = Toggle(bool(value))
            control.toggled.connect(lambda _c, f=fid: self._edit(f))
        elif k == 'choice':
            values = [o[0] if isinstance(o, tuple) else o for o in row.options]
            labels = [o[1] if isinstance(o, tuple) else o for o in row.options]
            plain = all(not isinstance(o, tuple) for o in row.options)
            current = labels[values.index(value)] if value in values else labels[0]
            control = SegmentedChoice(labels, current)
            control.currentChanged.connect(lambda _c, f=fid: self._edit(f))
            if plain:
                values = []
                labels = list(labels)
        elif k == 'select':
            control = Select([''], '')
            control.currentChanged.connect(lambda _c, f=fid: self._edit(f))
        elif k in ('num', 'decimal', 'text'):
            control = TextField(str(value), wide=row.wide, mono=row.wide)
            control.textChanged.connect(lambda _t, f=fid: self._edit(f))
        elif k == 'range':
            control = RangeSlider(float(value))
            control.valueChanged.connect(lambda _v, f=fid: self._edit(f))
        elif k == 'color':
            control = _ColorControl(value)
            control.colorChanged.connect(lambda h, f=fid: self.edits.edit(f, h))
        else:
            raise ValueError(f'unknown row kind {k!r}')
        bound = Bound(fid, row, None, control)  # type: ignore[arg-type]
        bound.values, bound.labels = values, labels
        return control, bound

    def _build_editable(self, row: Row) -> QWidget:
        fid = self._register(row)
        control, bound = self._control_for(row, fid)
        field_row = FieldRow(row.label, control, note=row.note, warn=row.warn, restart=row.restart)
        bound.field_row = field_row
        self.bound[fid] = bound
        self._rows[row.key] = field_row
        if row.kind == 'select':
            self._refresh_select(bound)
        return field_row

    def _build_display(self, row: Row) -> QWidget:
        src = row.source
        if src == 'camera_permission':
            control: QWidget = StatusDot('Checking…', 'dim')
            self._permission = control
        elif src == 'address_map':
            control = self._map_holder = _Holder()
            self._map_holder.set_rows([['Address', 'Payload', 'Rate']], ADDRESS_COLS)
        elif src == 'payload_sample':
            control = self._sample_block = CodeBlock('')
        elif src == 'frame_cost':
            control = self._cost_holder = _Holder()
            self._measure_button = Button('Measure')
            self._measure_button.clicked.connect(self.measure_costs)
            self._cost_holder.add_extra(self._measure_button)
            self._costs = {}
            self._render_costs()
        elif src == 'config_path':
            path = self.config.config_file
            home = os.path.expanduser('~')
            shown = ('~' + os.path.abspath(path)[len(home):]) if os.path.abspath(path).startswith(home) else path
            control = PathRow(shown)
            control.activated.connect(self.window_ref.reveal_config)
        else:
            raise ValueError(f'unknown source {src!r}')
        field_row = FieldRow(row.label, control, note=row.note, warn=row.warn, restart=row.restart)
        if src == 'payload_sample':
            self._sample_row = field_row
        if src:
            self._rows[f'@{src}'] = field_row
        return field_row

    # -- model <-> controls ----------------------------------------------
    def _edit(self, fid: str) -> None:
        if not self._syncing:
            self.edits.edit(fid, self.bound[fid].read())

    def _on_changed(self, fid: str) -> None:
        b = self.bound.get(fid)
        if b is not None:
            self._sync_one(b)
        self._after_change(fid)

    def _sync_one(self, b: Bound) -> None:
        value = self.edits.value(b.fid)
        self._syncing = True
        try:
            if b.row.kind == 'select':
                self._refresh_select(b)
            elif b.read() != value:
                b.write(value)
        finally:
            self._syncing = False
        dirty = self.edits.is_dirty(b.fid)
        b.field_row.set_dirty(dirty)
        b.field_row.set_error(self.edits.validate(b.fid) if dirty else None)

    def sync_all(self) -> None:
        """Write every pending (or saved) value into its control; close any open popup."""
        for b in self.bound.values():
            popup = getattr(b.control, '_popup', None)
            if popup is not None:
                popup.close()
        for b in self.bound.values():
            self._sync_one(b)
        self._after_change('')

    def _after_change(self, fid: str) -> None:
        """Cross-row effects of an edit."""
        if self.spec.key == 'input':
            if fid in ('', 'input.camera.use_ndi'):
                self._dim_sources()
            if fid in ('', 'input.camera.device_id'):
                b = self.bound.get('input.camera.device_id')
                if b is not None:
                    b.field_row.set_note(f'Device ID {self.edits.value("input.camera.device_id")}')
        elif self.spec.key == 'osc':
            # Built when first shown (it imports the OSC stack), kept current after
            if self.isVisible() and fid in ('', 'osc.osc.protocol', 'osc.ui.mode',
                                            'tracking.mediapipe.num_poses'):
                self.refresh_map()

    # -- selects (camera / NDI) ---------------------------------------------
    def _refresh_select(self, b: Bound) -> None:
        current = self.edits.value(b.fid)
        if b.row.source == 'camera_devices':
            names = self._camera_names
            try:
                cur = int(current)
            except (TypeError, ValueError):
                cur = 0
            ids = list(range(len(names))) if names else []
            if cur not in ids:
                ids.append(cur)
            ids.sort()
            labels = [f'Camera {i} — {names[i]}' if names and i < len(names) else f'Camera {i}'
                      for i in ids]
            values = ids
        else:
            found = list(self._ndi_sources or [])
            if current and current not in found:
                found.append(current)
            if found:
                values, labels = [''] + found, [_ANY_NDI] + found
            else:
                values, labels = [''], [_NO_NDI]
        b.values, b.labels = values, labels
        sel: Select = b.control  # type: ignore[assignment]
        label = labels[values.index(current)] if current in values else labels[0]
        sel.setOptions(labels, label)

    def _dim_sources(self) -> None:
        ndi = self.edits.value('input.camera.use_ndi') == 'NDI'
        cam = self.bound.get('input.camera.device_id')
        src = self.bound.get('input.camera.ndi_source')
        if cam:
            cam.control.set_dim(ndi)
        if src:
            src.control.set_dim(not ndi)

    # -- live data ----------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh_live_data()
        if self.spec.key == 'input' and not hasattr(self, '_ndi_timer'):
            self._ndi_timer = QTimer(self)
            self._ndi_timer.timeout.connect(self._poll_ndi)
            self._ndi_timer.start(NDI_REFRESH_MS)

    def refresh_live_data(self) -> None:
        """Kick off the background lookups this pane shows (cheap to call repeatedly)."""
        if self.spec.key == 'input':
            self._poll_camera()
            self._poll_ndi()
        elif self.spec.key == 'osc':
            self.refresh_map()
        elif self.spec.key == 'tracking':
            self._costs = sources.load_costs()
            self._render_costs()

    def _poll_camera(self) -> None:
        if self._camera_busy:
            return
        self._camera_busy = True

        def work():
            return sources.camera_names(), sources.camera_authorization()

        def done(result):
            self._camera_busy = False
            if result is not None:
                self._camera_names, self._camera_auth = result
            else:
                self._camera_names = self._camera_names or []
            self.set_permission()
            b = self.bound.get('input.camera.device_id')
            if b:
                self._refresh_select(b)

        self._async.run(work, done)

    def _poll_ndi(self) -> None:
        if self._ndi_busy or not self.isVisible():
            return
        if self.edits.value('input.camera.use_ndi') != 'NDI' and self._ndi_sources is not None:
            return
        self._ndi_busy = True

        def done(found):
            self._ndi_busy = False
            self._ndi_sources = sorted(found) if found else []
            b = self.bound.get('input.camera.ndi_source')
            if b:
                self._refresh_select(b)

        self._async.run(sources.ndi_sources, done)

    def set_permission(self) -> None:
        """Camera permission row from the macOS status and the device list."""
        if self._permission is None:
            return
        auth = self._camera_auth
        if auth == 'authorized':
            if self._camera_names == []:
                self._permission.set_state('Granted — no device present', 'warn')
            else:
                self._permission.set_state('Granted', 'accent')
        elif auth in ('denied', 'restricted'):
            self._permission.set_state('Denied by macOS', 'error')
        elif auth == 'undetermined':
            self._permission.set_state('Not asked yet', 'dim')
        else:
            self._permission.set_state('Unknown', 'dim')

    # -- OSC preview --------------------------------------------------------
    def _value_or_saved(self, fid: str, section: str, key: str, default=None):
        try:
            return self.edits.value(fid)
        except KeyError:
            return self.config.get(section, key, default)

    def refresh_map(self) -> None:
        """Rebuild the address map and payload sample from the pending format and mode."""
        if self._map_holder is None:
            return
        protocol = self._value_or_saved('osc.osc.protocol', 'osc', 'protocol', 'float')
        mode = self._value_or_saved('osc.ui.mode', 'ui', 'mode', 'all')
        try:
            num_poses = int(self._value_or_saved('tracking.mediapipe.num_poses', 'mediapipe', 'num_poses', 1))
        except (TypeError, ValueError):
            num_poses = 1
        rows = [['Address', 'Payload', 'Rate']] + [list(r) for r in sources.address_map(protocol, mode, num_poses)]
        self._map_holder.set_rows(rows, ADDRESS_COLS)
        if self._sample_block is not None:
            text, note = sources.payload_sample(protocol, mode)
            self._sample_block.setText(text)
            if self._sample_row is not None:
                self._sample_row.set_note(note)

    # -- frame cost -----------------------------------------------------------
    def _render_costs(self) -> None:
        if self._cost_holder is None:
            return
        rows = [['Model', 'Best for', 'Frame time']]
        for m in MODELS:
            ms = self._costs.get(m)
            rows.append([m, MODEL_BEST_FOR[m], f'{ms:.0f} ms' if ms is not None else '—'])
        self._cost_holder.set_rows(rows, COST_COLS)
        if self._measure_button is not None:
            self._measure_button.setText('Measuring…' if self._measuring
                                         else ('Measure again' if self._costs else 'Measure'))
            self._measure_button.setEnabled(not self._measuring)

    def measure_costs(self) -> None:
        """Measure lite, full and heavy in turn on a worker thread, updating the table as each lands."""
        if self._measuring:
            return
        self._measuring = True
        self._render_costs()
        pending = list(MODELS)

        def next_model(_result=None):
            if not pending:
                self._measuring = False
                self._costs = sources.load_costs()
                self._render_costs()
                return
            model = pending.pop(0)

            def done(ms, _m=model):
                if ms is not None:
                    self._costs[_m] = round(ms, 1)
                self._render_costs()
                next_model()

            self._async.run(lambda _m=model: sources.measure_cost(_m), done)

        next_model()

    # -- anchors ----------------------------------------------------------------
    def scroll_to_anchor(self, key: str) -> None:
        """
        Scroll to a row.

        Args:
            key: Config path (`camera.width`), field id (`input.camera.width`)
                or `@source` for display rows.
        """
        widget = self._rows.get(key) or self._rows.get(key.split('.', 1)[-1])
        if widget is not None:
            self.ensureWidgetVisible(widget, 0, 40)

    def field_row(self, key: str) -> Optional[FieldRow]:
        """The FieldRow for a config path (or `@source`), if any."""
        w = self._rows.get(key)
        return w if isinstance(w, FieldRow) else None
