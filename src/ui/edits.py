#!/usr/bin/env python3
"""
Pending-Change Model

Settings edits live here, beside the saved values, until Apply writes them to
config.json (once). Nothing in this module touches Qt widgets, so a settings
row only needs a field id to bind to: row <-> field id <-> control.

Field ids are `<pane>.<config key>`, e.g. `input.camera.width`; each maps to a
`(section, key)` config path (`camera`, `width`). Edit values are held in the
control's own form (str for numeric/text fields, bool, float, hex string) and
converted to the stored form only on Apply.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Signal

from ..config import Config

# ============================================================================
# VALIDATION
# ============================================================================
_IPV4 = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')
_HOSTNAME = re.compile(r'^(localhost|[a-z0-9-]+(\.[a-z0-9-]+)+)$', re.IGNORECASE)
_WHOLE = re.compile(r'^-?\d+$')


def validate_host(text: str) -> Optional[str]:
    """
    Validate an OSC host.

    Args:
        text: Host text as typed.

    Returns:
        An error message, or None if it is an IPv4 address, localhost or a hostname.
    """
    s = str(text).strip()
    if not s:
        return 'Cannot be empty'
    if _IPV4.match(s):
        return 'Each part of an IPv4 address is 0–255' if any(int(n) > 255 for n in s.split('.')) else None
    if _HOSTNAME.match(s):
        return None
    return 'Not a valid IPv4 address or hostname'


def validate_number(text: Any, limits: Optional[tuple] = None) -> Optional[str]:
    """
    Validate a whole-number field.

    Args:
        text: Value as typed.
        limits: (low, high) or (low, high, message); the message replaces
            the default `Must be between A and B`.

    Returns:
        An error message, or None.
    """
    s = str(text).strip()
    if not s:
        return 'Cannot be empty'
    if not _WHOLE.match(s):
        return 'Whole numbers only'
    if limits and (int(s) < limits[0] or int(s) > limits[1]):
        return limits[2] if len(limits) > 2 and limits[2] else 'Must be between {} and {}'.format(limits[0], limits[1])
    return None


def validate_decimal(text: Any, limits: Optional[tuple] = None) -> Optional[str]:
    """
    Validate a decimal-number field (e.g. seconds).

    Args:
        text: Value as typed.
        limits: (low, high) inclusive.

    Returns:
        An error message, or None.
    """
    s = str(text).strip()
    if not s:
        return 'Cannot be empty'
    try:
        v = float(s)
    except ValueError:
        return 'Numbers only'
    if v != v or v in (float('inf'), float('-inf')):
        return 'Numbers only'
    if limits and (v < limits[0] or v > limits[1]):
        return 'Must be between {} and {}'.format(limits[0], limits[1])
    return None


# ============================================================================
# FIELD
# ============================================================================
def _identity(v):
    return v


@dataclass
class Field:
    """
    One editable setting.

    Attributes:
        fid: `<pane>.<section>.<key>`.
        pane: Pane key (input, osc, ...).
        pane_title: Pane title for the bar's `Pane · Label` names.
        label: Row label.
        section, key: Config path.
        kind: num, decimal, text, choice, select, toggle, range or color.
        restart: True if the engine only reads it at startup.
        limits: (low, high[, message]) for num fields.
        decode: config value -> edit-form value.
        encode: edit-form value -> config value.
        validator: Optional extra callable(value) -> message | None.
    """
    fid: str
    pane: str
    pane_title: str
    label: str
    section: str
    key: str
    kind: str = 'text'
    restart: bool = False
    limits: Optional[tuple] = None
    decode: Callable[[Any], Any] = _identity
    encode: Callable[[Any], Any] = _identity
    validator: Optional[Callable[[Any], Optional[str]]] = None
    extra: dict = field(default_factory=dict)

    @property
    def config_key(self) -> str:
        """Dotted config key, as sent to `engine.set_live`."""
        return '{}.{}'.format(self.section, self.key)


# ============================================================================
# MODEL
# ============================================================================
class EditModel(QObject):
    """
    Pending edits over a Config.

    Signals:
        changed(fid): One field's edit changed ('' when many did).
        applied(list): Dirty field ids that were just written.
        reverted(): All edits were discarded.
    """

    changed = Signal(str)
    applied = Signal(list)
    reverted = Signal()

    def __init__(self, config: Config, engine=None, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self.engine = engine
        self._fields: dict[str, Field] = {}
        self._edits: dict[str, Any] = {}

    # -- registration -----------------------------------------------------
    def register(self, pane: str, pane_title: str, label: str, path: str, *, kind: str = 'text',
                 restart: bool = False, limits: Optional[tuple] = None, decode=None, encode=None,
                 validator=None, **extra) -> str:
        """
        Register a field and return its id.

        Args:
            pane: Pane key.
            pane_title: Pane title (for bar names).
            label: Row label.
            path: Config path `section.key`.
            kind: num, text, choice, toggle, range or color.
            restart: Restart-class field.
            limits: (low, high[, message]) for num fields.
            decode, encode: Config <-> edit-form conversions (default: str for
                num, identity otherwise).
            validator: Extra callable(value) -> message | None.
            extra: Free-form row data kept on the field.

        Returns:
            The field id `<pane>.<path>`.
        """
        section, key = path.split('.', 1)
        if decode is None:
            decode = (lambda v: str(v)) if kind in ('num', 'decimal') else _identity
        if encode is None:
            encode = {'num': lambda v: int(str(v).strip()),
                      'decimal': lambda v: float(str(v).strip())}.get(kind, _identity)
        fid = '{}.{}'.format(pane, path)
        self._fields[fid] = Field(fid, pane, pane_title, label, section, key, kind, restart,
                                  limits, decode, encode, validator, extra)
        return fid

    def field(self, fid: str) -> Field:
        return self._fields[fid]

    def fields(self) -> list[Field]:
        return list(self._fields.values())

    # -- values -----------------------------------------------------------
    def saved(self, fid: str) -> Any:
        """The saved (applied) value in edit form."""
        f = self._fields[fid]
        return f.decode(self.config.get(f.section, f.key))

    def value(self, fid: str) -> Any:
        """The pending value if edited, else the saved one."""
        return self._edits[fid] if fid in self._edits else self.saved(fid)

    def edit(self, fid: str, value: Any) -> None:
        """
        Set a pending value. Editing back to the saved value clears the edit.

        Args:
            fid: Field id.
            value: Value in edit form.
        """
        if fid not in self._fields:
            raise KeyError(fid)
        if value == self.saved(fid):
            changed = self._edits.pop(fid, None) is not None
        else:
            changed = self._edits.get(fid, object()) != value
            self._edits[fid] = value
        if changed:
            self.changed.emit(fid)

    def is_dirty(self, fid: Optional[str] = None) -> bool:
        """True if `fid` (or, with no argument, any field) has a pending change."""
        if fid is None:
            return any(self.is_dirty(f) for f in self._edits)
        return fid in self._edits and self._edits[fid] != self.saved(fid)

    def validate(self, fid: str) -> Optional[str]:
        """Error message for the field's pending value, or None."""
        f = self._fields[fid]
        v = self.value(fid)
        if f.kind == 'num':
            msg = validate_number(v, f.limits)
        elif f.kind == 'decimal':
            msg = validate_decimal(v, f.limits)
        elif f.kind == 'text':
            msg = validate_host(v) if f.extra.get('host') else (None if str(v).strip() else 'Cannot be empty')
        else:
            msg = None
        if msg is None and f.validator is not None:
            msg = f.validator(v)
        return msg

    def dirty_list(self) -> list[dict]:
        """
        Dirty fields in registration order.

        Returns:
            Dicts: fid, pane, pane_title, label, value, restart, error.
        """
        out = []
        for fid, f in self._fields.items():
            if self.is_dirty(fid):
                out.append({'fid': fid, 'pane': f.pane, 'pane_title': f.pane_title, 'label': f.label,
                            'value': self._edits[fid], 'restart': f.restart, 'error': self.validate(fid)})
        return out

    def restart_count(self) -> int:
        return sum(1 for d in self.dirty_list() if d['restart'])

    def invalid_count(self) -> int:
        return sum(1 for d in self.dirty_list() if d['error'])

    # -- actions ----------------------------------------------------------
    def revert(self) -> None:
        """Discard every pending edit."""
        had = bool(self._edits)
        self._edits.clear()
        if had:
            self.reverted.emit()

    def apply(self) -> Optional[bool]:
        """
        Write the dirty fields to config.json (one Config.save), push live-class
        values to a running engine, and restart it if a restart-class field changed.

        Returns:
            None if nothing was applied (no edits, or any edit invalid);
            otherwise True if the engine was restarted, else False.
        """
        dirty = self.dirty_list()
        if not dirty or any(d['error'] for d in dirty):
            return None
        for d in dirty:
            f = self._fields[d['fid']]
            self.config.set(f.section, f.key, f.encode(d['value']))
        self.config.save()
        self._edits.clear()

        running = bool(self.engine is not None and self.engine.is_running())
        restarted = False
        if running:
            if any(d['restart'] for d in dirty):
                self.engine.restart()
                restarted = True
            else:
                for d in dirty:
                    f = self._fields[d['fid']]
                    self.engine.set_live(f.config_key, self.config.get(f.section, f.key))
        self.applied.emit([d['fid'] for d in dirty])
        return restarted
