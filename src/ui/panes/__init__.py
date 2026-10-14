"""
Pane registry.

Panes are listed in rail order. Each module exposes `create(window) -> QWidget`
and is imported statically (not via importlib) so the frozen app bundles it
without a hiddenimports entry.
"""

from . import advanced, help, input, live, osc, preview, tracking  # noqa: A004

#: (key, rail label, module) in rail order
PANES = (
    ('live', 'Live', live),
    ('input', 'Input', input),
    ('osc', 'OSC Output', osc),
    ('tracking', 'Tracking', tracking),
    ('preview', 'Preview', preview),
    ('advanced', 'Advanced', advanced),
    ('help', 'Help', help),
)

KEYS = tuple(k for k, _, _ in PANES)
