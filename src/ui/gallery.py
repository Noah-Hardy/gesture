#!/usr/bin/env python3
"""
Widget Gallery

Renders every widget in every state so each can be reviewed without the full
app:

    python -m src.ui.gallery                  # open the window
    python -m src.ui.gallery --shot out.png   # render offscreen to a PNG and exit
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import argparse
import math
import sys

from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from . import tokens
from .tokens import Font, color
from .widgets import (
    Button, CodeBlock, ColorSwatch, FieldRow, Meter, PathRow, RangeSlider, SegmentedChoice,
    Select, Sparkline, SpecTable, StatusDot, TextField, Toggle,
)

OPTS = ['Built-in Camera', 'Continuity Camera', 'NDI: STUDIO-PC (Output)']


# ============================================================================
# BUILDERS
# ============================================================================
def _heading(text: str) -> QLabel:
    lab = QLabel(text.upper())
    f = Font.ui(11)
    f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.5)
    lab.setFont(f)
    lab.setStyleSheet(f"color: {color('accent').name()}; background: transparent; padding: 14px 0 4px 0;")
    return lab


def _ring(w: QWidget) -> QWidget:
    """Force the focus ring on for static screenshots."""
    w.setProperty('ringPreview', True)
    return w


def _section(layout: QVBoxLayout, title: str) -> None:
    layout.addWidget(_heading(title))


def build_gallery() -> QWidget:
    """
    Build the gallery content widget.

    Returns:
        A QWidget holding every widget in every state. Widgets that are
        meant to be Tab-reachable are real, focusable children.
    """
    root = QWidget()
    root.setObjectName('galleryRoot')
    root.setStyleSheet(f"QWidget#galleryRoot {{ background: {color('bg_window').name()}; }}")
    v = QVBoxLayout(root)
    v.setContentsMargins(40, 12, 40, 30)
    v.setSpacing(0)

    # -- row-level widgets, with ring preview -----------------------------
    _section(v, 'Toggle  (off / on / focused / focused on)')
    h = QHBoxLayout()
    h.setSpacing(6)
    h.addWidget(Toggle(False))
    h.addWidget(Toggle(True))
    h.addWidget(_ring(Toggle(False)))
    h.addWidget(_ring(Toggle(True)))
    h.addStretch(1)
    v.addLayout(h)

    _section(v, 'RangeSlider  (0.00 / 0.50 / dirty / dragging-focus / 1.00)')
    for val, dirty, ring in ((0.0, False, False), (0.5, False, False), (0.65, True, False),
                             (0.35, False, True), (1.0, False, False)):
        s = RangeSlider(val)
        s.set_dirty(dirty)
        if ring:
            _ring(s._track)
        v.addWidget(s)

    _section(v, 'SegmentedChoice  (clean / dirty / focused)')
    for dirty, ring in ((False, False), (True, False), (False, True)):
        c = SegmentedChoice(['lite', 'full', 'heavy'], 'full')
        c.set_dirty(dirty)
        if ring:
            _ring(c)
        h = QHBoxLayout()
        h.addWidget(c)
        h.addStretch(1)
        v.addLayout(h)

    _section(v, 'Select  (clean / dirty / dim / focused)  click to open the popup')
    h = QHBoxLayout()
    h.setSpacing(6)
    a = Select(OPTS, OPTS[0])
    b = Select(OPTS, OPTS[1])
    b.set_dirty(True)
    d = Select(['Auto', 'CPU', 'GPU'], 'Auto')
    d.set_dim(True)
    f = _ring(Select(OPTS, OPTS[2]))
    for w in (a, b):
        h.addWidget(w)
    h.addStretch(1)
    v.addLayout(h)
    h = QHBoxLayout()
    h.setSpacing(6)
    for w in (d, f):
        h.addWidget(w)
    h.addStretch(1)
    v.addLayout(h)

    _section(v, 'TextField  (clean / dirty / error / wide / focused / focused + error)')
    h = QHBoxLayout()
    h.setSpacing(6)
    t1 = TextField('1234')
    t2 = TextField('1235')
    t2.set_dirty(True)
    t3 = TextField('99999')
    t3.set_error(True)
    t4 = TextField('127.0.0.1', wide=True)
    t5 = _ring(TextField('0'))
    t6 = TextField('-1')
    t6.set_error(True)
    _ring(t6)
    for w in (t1, t2, t3, t4, t5, t6):
        h.addWidget(w)
    h.addStretch(1)
    v.addLayout(h)

    _section(v, 'Button  (default / primary / focused)')
    h = QHBoxLayout()
    h.setSpacing(6)
    h.addWidget(Button('Console'))
    h.addWidget(Button('Start', primary=True))
    h.addWidget(_ring(Button('Reveal')))
    h.addStretch(1)
    v.addLayout(h)

    # -- FieldRow states ---------------------------------------------------
    _section(v, 'FieldRow  (plain / note / dirty / dirty + restart / warn / error)')
    rows = QVBoxLayout()
    rows.setSpacing(0)

    t = Toggle(True)
    rows.addWidget(FieldRow('Draw landmarks over the feed', t))

    t = Toggle(False)
    rows.addWidget(FieldRow('Render preview in background', t, note='Tracking continues either way'))

    t = Toggle(True)
    r = FieldRow('Show FPS / stats line', t)
    r.set_dirty(True)
    rows.addWidget(r)

    t = Toggle(True)
    r = FieldRow('Force CPU delegate', t, restart=True)
    r.set_dirty(True)
    rows.addWidget(r)

    t = Toggle(True)
    r = FieldRow('Force GPU delegate', t, restart=True, warn='Leaks memory on Apple Silicon')
    r.set_dirty(True)
    rows.addWidget(r)

    sel = Select(OPTS, OPTS[1])
    r = FieldRow('Camera', sel, restart=True, note='Restarts the engine when applied')
    r.set_dirty(True)
    rows.addWidget(r)

    tf = TextField('99999')
    r = FieldRow('Port', tf, restart=True)
    r.set_dirty(True)
    r.set_error('Port must be 1-65535')
    rows.addWidget(r)

    sl = RangeSlider(0.65)
    r = FieldRow('Detection confidence', sl, note='Higher rejects more false positives')
    r.set_dirty(True)
    sl.valueChanged.connect(lambda _v: None)
    rows.addWidget(r)

    seg = SegmentedChoice(['lite', 'full', 'heavy'], 'heavy')
    r = FieldRow('Model', seg, restart=True)
    r.set_dirty(True)
    rows.addWidget(r)

    rows.addWidget(FieldRow('Camera access', StatusDot('Granted', 'accent')))
    rows.addWidget(FieldRow('Camera access', StatusDot('Denied by macOS', 'error')))
    rows.addWidget(FieldRow('Camera access', StatusDot('Granted — no device present', 'warn')))
    rows.addWidget(FieldRow('Overlay colour', ColorSwatch('#3DDC84')))
    rows.addWidget(FieldRow('Config file',
                            PathRow('/Users/noah/Library/Application Support/Gesture/config.json')))
    rows.addWidget(FieldRow('Example payload', CodeBlock(
        '/pose/world  [x, y, z, visibility] × 33\n/hand/left  [x, y, z] × 21')))
    v.addLayout(rows)

    # -- display widgets ---------------------------------------------------
    _section(v, 'Meter')
    g = QGridLayout()
    g.setSpacing(10)
    for i, (lab, val, unit, frac) in enumerate((
            ('Frame rate', '29.4', 'fps', 0.98), ('Latency', '31', 'ms', 0.3),
            ('Pose', '33', 'landmarks', 1.0), ('Hands', '0', '', 0.0))):
        g.addWidget(Meter(lab, val, unit, frac), 0, i)
    v.addLayout(g)

    _section(v, 'Sparkline  (active / idle)')
    h = QHBoxLayout()
    h.setSpacing(30)
    s1 = Sparkline()
    s1.set_values([0.28 + 0.72 * abs(math.sin(1.7 + i * 0.9)) for i in range(22)])
    s2 = Sparkline()
    h.addWidget(s1)
    h.addWidget(s2)
    h.addStretch(1)
    v.addLayout(h)

    _section(v, 'SpecTable')
    v.addWidget(SpecTable([
        ['Address', 'Payload', 'Rate'],
        ['/pose/raw', 'Normalized landmarks, 33 × [x y z vis]', '30/s'],
        ['/pose/world', 'World coordinates in metres', '30/s'],
        ['/pose/bounds', 'Bounding box of the tracked pose', '30/s'],
        ['/hand/left', 'Left hand landmarks, 21 × [x y z]', '30/s'],
    ]))
    v.addStretch(1)
    return root


def build_window() -> QScrollArea:
    """The gallery in a scroll area, sized to show most of it."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWindowTitle('Gesture widget gallery')
    area.setWidget(build_gallery())
    area.setStyleSheet(f"QScrollArea {{ background: {color('bg_window').name()}; }}")
    area.resize(860, 900)
    return area


# ============================================================================
# ENTRY
# ============================================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--shot', help='render the full gallery to this PNG and exit')
    args = ap.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    tokens.apply(app)
    win = build_window()
    if args.shot:
        content = win.widget()
        content.setFixedWidth(860)
        content.adjustSize()
        content.resize(860, content.sizeHint().height())
        pm = content.grab()
        pm.save(args.shot)
        print(f'{args.shot}: {pm.width()}x{pm.height()}')
        return 0
    win.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

