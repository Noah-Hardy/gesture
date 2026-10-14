#!/usr/bin/env python3
"""
Config Table Generator (#75, #40)

Renders the settings table in docs/appendix-advanced.md from the declarative
row spec in src/ui/panes/spec.py, so the documentation cannot drift from the
panes. A test compares the committed block with `render()`.

Usage:
    QT_QPA_PLATFORM=offscreen uv run python scripts/gen_config_table.py          # print
    QT_QPA_PLATFORM=offscreen uv run python scripts/gen_config_table.py --write  # update the appendix
"""

# ============================================================================
# IMPORTS
# ============================================================================
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Config
from src.live_keys import is_app_only, is_live
from src.ui.panes.spec import PANES

APPENDIX = os.path.join(ROOT, 'docs', 'appendix-advanced.md')
BEGIN = '<!-- BEGIN GENERATED: config-table (scripts/gen_config_table.py) -->'
END = '<!-- END GENERATED: config-table -->'

_TYPES = {'choice': 'choice', 'select': 'choice', 'num': 'integer', 'decimal': 'number',
          'text': 'string', 'toggle': 'boolean', 'range': 'number', 'color': 'colour (BGR list)'}


# ============================================================================
# RENDERING
# ============================================================================
def _default(row) -> str:
    section, name = row.key.split('.', 1)
    value = Config.DEFAULT_CONFIG.get(section, {}).get(name, row.default)
    if value is None:
        return ''
    if isinstance(value, bool):
        return '`true`' if value else '`false`'
    if isinstance(value, list):
        return '`[{}]`'.format(', '.join(str(v) for v in value))
    return '`{}`'.format(value) if value != '' else '`""`'


def _range(row) -> str:
    if row.kind == 'range':
        return '0 to 1'
    if row.kind in ('num', 'decimal') and row.limits:
        return '{} to {}'.format(row.limits[0], row.limits[1])
    if row.kind == 'choice':
        if row.key == 'camera.use_ndi':
            return '`false` (Camera), `true` (NDI)'
        return ', '.join('`{}`'.format(o[0] if isinstance(o, tuple) else o) for o in row.options)
    if row.kind == 'select':
        return 'device index' if row.key == 'camera.device_id' else 'source name'
    if row.kind == 'text':
        return 'host name or IP address'
    return ''


def render() -> str:
    """
    The generated table block, including its begin and end comments.

    Returns:
        Markdown with one row per setting that has a config key, in pane order.
    """
    lines = [BEGIN, '',
             '| Key | Pane | Type | Range or options | Default | Restart |',
             '|---|---|---|---|---|---|']
    for pane in PANES.values():
        for row in pane.rows:
            if not row.key:
                continue
            restart = row.restart or not is_live(row.key)
            lines.append('| `{}` | {} | {} | {} | {} | {} |'.format(
                row.key, pane.title, _TYPES.get(row.kind, row.kind), _range(row),
                _default(row),
                'app' if is_app_only(row.key) else 'restart' if restart else 'live'))
    lines += ['', END]
    return '\n'.join(lines)


def update(text: str) -> str:
    """Return `text` with the generated block replaced by a fresh render()."""
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + render() + text[end:]


def main() -> None:
    if '--write' in sys.argv:
        with open(APPENDIX, encoding='utf-8') as f:
            text = f.read()
        with open(APPENDIX, 'w', encoding='utf-8') as f:
            f.write(update(text))
    else:
        print(render())


if __name__ == '__main__':
    main()
