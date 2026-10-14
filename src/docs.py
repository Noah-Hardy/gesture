#!/usr/bin/env python3
"""
Documentation Model, Parser and Renderers

Parses a small, total Markdown subset into a plain data model, then renders
that model three ways: tk.Text insert instructions for the legacy Tk Help
window, a QTextDocument for the Qt Help pane, and a self-contained HTML page
for "Open Full Documentation in Browser".

Deliberately stdlib-only and free of any `src.*` import. src/__init__.py
eagerly imports mediapipe, cv2 and NDIlib, so importing this module must not
drag in the whole ML stack just to read a Markdown file - it should stay
cheap enough to load and check in isolation.
"""

# ============================================================================
# IMPORTS
# ============================================================================
import html as _html
import os
import plistlib
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from src.config import app_support_dir


# ============================================================================
# TOPICS
# ============================================================================
@dataclass(frozen=True)
class Topic:
    """One documentation topic: a sidebar/menu entry backed by one Markdown file"""
    slug: str
    label: str
    group: str          # 'Guide' or 'Reference'
    filename: str


TOPICS: Tuple[Topic, ...] = (
    Topic('welcome', 'Welcome', 'Guide', 'welcome.md'),
    Topic('quick-start', 'Quick Start', 'Guide', 'quick-start.md'),
    Topic('settings', 'Settings', 'Guide', 'settings.md'),
    Topic('updates', 'Updates', 'Guide', 'updates.md'),
    Topic('input-sources', 'Camera & NDI', 'Guide', 'input-sources.md'),
    Topic('osc-output', 'OSC Output', 'Guide', 'osc-output.md'),
    Topic('receivers', 'TouchDesigner, Max, Unity, Isadora', 'Guide', 'receivers.md'),
    Topic('models-performance', 'Models & Performance', 'Guide', 'models-performance.md'),
    Topic('troubleshooting', 'Troubleshooting', 'Guide', 'troubleshooting.md'),
    Topic('osc-reference', 'OSC Address Reference', 'Reference', 'osc-reference.md'),
    Topic('appendix-advanced', 'Appendix: CLI & config.json', 'Reference', 'appendix-advanced.md'),
)


def topic_by_slug(slug: str) -> Topic:
    """Look up a topic by slug, raising KeyError with a clear message if unknown"""
    for topic in TOPICS:
        if topic.slug == slug:
            return topic
    raise KeyError(f"Unknown documentation topic: {slug!r}")


# ============================================================================
# PATH RESOLUTION
# ============================================================================
def docs_dir() -> str:
    """Read-only directory holding the Markdown shipped with the app

    Mirrors src.model_downloader._bundled_tasks_dir: sys._MEIPASS/docs when
    frozen (PyInstaller cross-links this to Contents/Resources/docs), or the
    repo's docs/ directory from source. Never resolved relative to cwd - the
    engine subprocess deliberately runs with cwd unset when frozen
    (see src/gui.py _build_command).
    """
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'docs')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs')


def read_topic(slug: str) -> str:
    """Read the raw Markdown source for one topic"""
    topic = topic_by_slug(slug)
    path = os.path.join(docs_dir(), topic.filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ============================================================================
# DATA MODEL
# ============================================================================
@dataclass(frozen=True)
class Span:
    """One run of inline-styled text"""
    text: str
    style: str = 'text'     # 'text' | 'code' | 'bold' | 'link'
    href: str = ''


@dataclass(frozen=True)
class Block:
    """One parsed block-level element"""
    kind: str                                  # h1|h2|h3|p|bullet|number|code|table|rule
    spans: Tuple[Span, ...] = ()
    text: str = ''                              # 'code' only, verbatim
    lang: str = ''                               # 'code' only
    level: int = 0                               # list nesting depth (0 or 1)
    rows: Tuple[Tuple[Tuple[Span, ...], ...], ...] = ()   # 'table', row 0 = header
    aligns: Tuple[str, ...] = field(default_factory=tuple)  # 'table', from :---: markers


# ============================================================================
# INLINE PARSING
# ============================================================================
# Precedence: inline code first (so `**not bold**` inside backticks stays
# literal), then bold, then links. No nesting, no italics, no images, no raw
# HTML. Links are restricted to http(s)/mailto at parse time so neither
# renderer can ever be handed a javascript: or file: URL from doc source.
_INLINE_RE = re.compile(
    r'`(?P<code>[^`]+)`'
    r'|\*\*(?P<bold>[^*]+?)\*\*'
    r'|\[(?P<link_text>[^\]]+)\]\((?P<link_url>(?:https?://|mailto:)[^)\s]+)\)'
)


def parse_inline(text: str) -> Tuple[Span, ...]:
    """Split one line of text into styled spans"""
    spans = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            spans.append(Span(text[pos:m.start()]))
        if m.group('code') is not None:
            spans.append(Span(m.group('code'), style='code'))
        elif m.group('bold') is not None:
            spans.append(Span(m.group('bold'), style='bold'))
        else:
            spans.append(Span(m.group('link_text'), style='link', href=m.group('link_url')))
        pos = m.end()
    if pos < len(text):
        spans.append(Span(text[pos:]))
    return tuple(spans)


# ============================================================================
# BLOCK PARSING
# ============================================================================
_HEADING_RE = re.compile(r'^(#{1,3})\s+(.*)$')
_BULLET_RE = re.compile(r'^(\s*)-\s+(.*)$')
_NUMBER_RE = re.compile(r'^(\s*)\d+\.\s+(.*)$')
_FENCE_RE = re.compile(r'^```\s*(\S*)\s*$')
_TABLE_SEP_RE = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')


def _split_table_row(line: str) -> Tuple[str, ...]:
    """Split one `| a | b |` line into trimmed cell strings"""
    trimmed = line.strip()
    if trimmed.startswith('|'):
        trimmed = trimmed[1:]
    if trimmed.endswith('|'):
        trimmed = trimmed[:-1]
    return tuple(cell.strip() for cell in trimmed.split('|'))


def _table_aligns(sep_line: str, ncols: int) -> Tuple[str, ...]:
    """Read column alignment markers (:---, :---:, ---:) from the separator row"""
    cells = _split_table_row(sep_line)
    aligns = []
    for cell in cells:
        left = cell.startswith(':')
        right = cell.endswith(':')
        if left and right:
            aligns.append('center')
        elif right:
            aligns.append('right')
        else:
            aligns.append('left')
    while len(aligns) < ncols:
        aligns.append('left')
    return tuple(aligns[:ncols])


def parse(markdown: str) -> Tuple[Block, ...]:
    """Parse the supported Markdown subset into a tuple of Blocks

    Never raises on unrecognized syntax - anything outside the subset falls
    through to a plain paragraph, because every byte of source is authored
    in-repo and a parser that can crash on a typo is a worse failure mode
    than a slightly-wrong paragraph.
    """
    lines = markdown.splitlines()
    blocks = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if line.strip() == '---':
            blocks.append(Block(kind='rule'))
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            kind = {1: 'h1', 2: 'h2', 3: 'h3'}[level]
            blocks.append(Block(kind=kind, spans=parse_inline(heading.group(2).strip())))
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            lang = fence.group(1)
            i += 1
            code_lines = []
            while i < n and not _FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            blocks.append(Block(kind='code', text='\n'.join(code_lines), lang=lang))
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            indent, content = bullet.groups()
            level = 1 if len(indent) >= 2 else 0
            blocks.append(Block(kind='bullet', spans=parse_inline(content), level=level))
            i += 1
            continue

        number = _NUMBER_RE.match(line)
        if number:
            indent, content = number.groups()
            level = 1 if len(indent) >= 2 else 0
            blocks.append(Block(kind='number', spans=parse_inline(content), level=level))
            i += 1
            continue

        if '|' in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1] or ''):
            header_cells = _split_table_row(line)
            aligns = _table_aligns(lines[i + 1], len(header_cells))
            rows = [tuple(parse_inline(c) for c in header_cells)]
            i += 2
            while i < n and '|' in lines[i] and lines[i].strip():
                rows.append(tuple(parse_inline(c) for c in _split_table_row(lines[i])))
                i += 1
            blocks.append(Block(kind='table', rows=tuple(rows), aligns=aligns))
            continue

        # Paragraph: collect contiguous non-blank, non-special lines
        para_lines = [line]
        i += 1
        while i < n and lines[i].strip() and not any((
            _HEADING_RE.match(lines[i]),
            _BULLET_RE.match(lines[i]),
            _NUMBER_RE.match(lines[i]),
            _FENCE_RE.match(lines[i]),
            lines[i].strip() == '---',
        )):
            para_lines.append(lines[i])
            i += 1
        blocks.append(Block(kind='p', spans=parse_inline(' '.join(l.strip() for l in para_lines))))

    return tuple(blocks)


def load_topic(slug: str) -> Tuple[Block, ...]:
    """Read and parse one topic's Markdown source"""
    return parse(read_topic(slug))


# ============================================================================
# HEADING ANCHORS
# ============================================================================
def heading_slug(text: str) -> str:
    """Anchor slug for a heading's plain text

    The one slug rule shared by render_html() and render_qt(), so a
    `help://<topic>#<anchor>` link and the standalone page agree. Lowercase,
    every run of non-alphanumeric characters becomes a single '-', leading and
    trailing '-' are stripped, and an empty result falls back to 'section'.
    Example: 'Show preview window' -> 'show-preview-window'.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug or 'section'


def heading_anchors(blocks: Tuple[Block, ...]) -> Dict[int, str]:
    """Map block index -> anchor slug for every `##` (h2) heading in a topic

    Duplicate slugs within one topic get '-2', '-3', ... suffixes, in document
    order, so every anchor is unique.
    """
    anchors: Dict[int, str] = {}
    seen: Dict[str, int] = {}
    for idx, block in enumerate(blocks):
        if block.kind != 'h2':
            continue
        base = heading_slug(_flatten_plain(block.spans))
        count = seen.get(base, 0) + 1
        seen[base] = count
        anchors[idx] = base if count == 1 else f'{base}-{count}'
    return anchors


# ============================================================================
# TK RENDERER
# ============================================================================
TK_TAGS: Tuple[str, ...] = (
    'body', 'h1', 'h2', 'h3', 'bullet', 'number', 'code_block',
    'code_inline', 'bold', 'link', 'rule', 'table',
)


@dataclass(frozen=True)
class TkPlan:
    """Render output for the tk.Text viewer: insert chunks plus link targets"""
    chunks: Tuple[Tuple[str, Tuple[str, ...]], ...]
    links: Dict[str, str] = field(default_factory=dict)


def _flatten_plain(spans: Tuple[Span, ...]) -> str:
    """Strip inline styling markers, keeping only the literal text (for tables)"""
    return ''.join(s.text for s in spans)


def _table_col_widths(rows, max_width: int = 46) -> Tuple[int, ...]:
    ncols = len(rows[0]) if rows else 0
    widths = [0] * ncols
    for row in rows:
        for idx, cell in enumerate(row):
            if idx >= ncols:
                continue
            text = _flatten_plain(cell)
            if len(text) > max_width:
                text = text[: max_width - 1] + '…'
            widths[idx] = max(widths[idx], len(text))
    return tuple(widths)


def _table_cell_text(cell: Tuple[Span, ...], width: int, max_width: int = 46) -> str:
    text = _flatten_plain(cell)
    if len(text) > max_width:
        text = text[: max_width - 1] + '…'
    return text.ljust(width)


def render_tk(blocks: Tuple[Block, ...], width_chars: int = 80) -> TkPlan:
    """Render parsed blocks into tk.Text insert instructions

    Returns chunks of (text, tag_names) ready for repeated text.insert('end',
    chunk, tags) calls, plus a link-tag -> href map (hrefs can't ride on a
    shared tk tag, so each link gets its own numbered tag alongside 'link').
    """
    chunks = []
    links: Dict[str, str] = {}
    link_counter = 0

    def emit_spans(spans, base_tags):
        nonlocal link_counter
        for span in spans:
            if span.style == 'code':
                chunks.append((span.text, base_tags + ('code_inline',)))
            elif span.style == 'bold':
                chunks.append((span.text, base_tags + ('bold',)))
            elif span.style == 'link':
                tag = f'link-{link_counter}'
                link_counter += 1
                links[tag] = span.href
                chunks.append((span.text, base_tags + ('link', tag)))
            else:
                chunks.append((span.text, base_tags))

    for block in blocks:
        if block.kind in ('h1', 'h2', 'h3'):
            emit_spans(block.spans, (block.kind,))
            chunks.append(('\n', (block.kind,)))
        elif block.kind == 'p':
            emit_spans(block.spans, ('body',))
            chunks.append(('\n\n', ('body',)))
        elif block.kind in ('bullet', 'number'):
            prefix = ('    ' if block.level else '') + ('• ' if block.kind == 'bullet' else '1. ')
            chunks.append((prefix, (block.kind,)))
            emit_spans(block.spans, (block.kind,))
            chunks.append(('\n', (block.kind,)))
        elif block.kind == 'code':
            chunks.append((block.text + '\n', ('code_block',)))
        elif block.kind == 'rule':
            chunks.append(('─' * min(width_chars, 60) + '\n\n', ('rule',)))
        elif block.kind == 'table':
            widths = _table_col_widths(block.rows)
            total = sum(widths) + 3 * (len(widths) - 1) if widths else 0
            if total <= width_chars:
                header = block.rows[0]
                line = ' │ '.join(
                    _table_cell_text(c, w) for c, w in zip(header, widths)
                )
                chunks.append((line + '\n', ('table',)))
                rule = '─┼─'.join('─' * w for w in widths)
                chunks.append((rule + '\n', ('table',)))
                for row in block.rows[1:]:
                    line = ' │ '.join(
                        _table_cell_text(c, w) for c, w in zip(row, widths)
                    )
                    chunks.append((line + '\n', ('table',)))
                chunks.append(('\n', ('table',)))
            else:
                # Stacked fallback: one paragraph per row, "header: value" per line
                header = [_flatten_plain(c) for c in block.rows[0]]
                for row in block.rows[1:]:
                    for h, cell in zip(header, row):
                        chunks.append((f'{h}: ', ('bold',)))
                        emit_spans(cell, ('body',))
                        chunks.append(('\n', ('body',)))
                    chunks.append(('\n', ('body',)))

    return TkPlan(chunks=tuple(chunks), links=links)


# ============================================================================
# HTML RENDERER
# ============================================================================
def _esc(text: str) -> str:
    return _html.escape(text, quote=True)


def _spans_to_html(spans: Tuple[Span, ...]) -> str:
    parts = []
    for span in spans:
        if span.style == 'code':
            parts.append(f'<code>{_esc(span.text)}</code>')
        elif span.style == 'bold':
            parts.append(f'<strong>{_esc(span.text)}</strong>')
        elif span.style == 'link':
            parts.append(f'<a href="{_esc(span.href)}">{_esc(span.text)}</a>')
        else:
            parts.append(_esc(span.text))
    return ''.join(parts)


def render_html(blocks: Tuple[Block, ...], anchor_prefix: str = '') -> str:
    """Render parsed blocks into an HTML body fragment for one topic

    Args:
        blocks: Parsed topic.
        anchor_prefix: Prepended to each `##` heading's id (see
            heading_anchors); render_site passes '<slug>--' so ids stay unique
            across the one-page site.
    """
    anchors = heading_anchors(blocks)
    out = []
    list_open = None  # 'ul' | 'ol' | None

    def close_list():
        if list_open is not None:
            out.append(f'</{list_open}>')
        return None

    for idx, block in enumerate(blocks):
        if block.kind not in ('bullet', 'number'):
            list_open = close_list()

        if block.kind == 'h2':
            out.append(f'<h2 id="{_esc(anchor_prefix + anchors[idx])}">{_spans_to_html(block.spans)}</h2>')
        elif block.kind in ('h1', 'h3'):
            out.append(f'<{block.kind}>{_spans_to_html(block.spans)}</{block.kind}>')
        elif block.kind == 'p':
            out.append(f'<p>{_spans_to_html(block.spans)}</p>')
        elif block.kind == 'bullet':
            if list_open != 'ul':
                list_open = close_list()
                out.append('<ul>')
                list_open = 'ul'
            out.append(f'<li>{_spans_to_html(block.spans)}</li>')
        elif block.kind == 'number':
            if list_open != 'ol':
                list_open = close_list()
                out.append('<ol>')
                list_open = 'ol'
            out.append(f'<li>{_spans_to_html(block.spans)}</li>')
        elif block.kind == 'code':
            lang_class = f' class="language-{_esc(block.lang)}"' if block.lang else ''
            out.append(f'<pre><code{lang_class}>{_esc(block.text)}</code></pre>')
        elif block.kind == 'rule':
            out.append('<hr>')
        elif block.kind == 'table':
            out.append('<table><thead><tr>')
            for cell, align in zip(block.rows[0], block.aligns):
                style = f' style="text-align:{align}"' if align != 'left' else ''
                out.append(f'<th{style}>{_spans_to_html(cell)}</th>')
            out.append('</tr></thead><tbody>')
            for row in block.rows[1:]:
                out.append('<tr>')
                for idx, cell in enumerate(row):
                    align = block.aligns[idx] if idx < len(block.aligns) else 'left'
                    style = f' style="text-align:{align}"' if align != 'left' else ''
                    out.append(f'<td{style}>{_spans_to_html(cell)}</td>')
                out.append('</tr>')
            out.append('</tbody></table>')

    close_list()
    return '\n'.join(out)


# ============================================================================
# QT RENDERER
# ============================================================================
def _qt_spans(spans: Tuple[Span, ...], pal: Dict[str, str], mono: str) -> str:
    parts = []
    for span in spans:
        if span.style == 'code':
            parts.append(
                f'<span style="font-family:\'{mono}\'; font-size:12px; '
                f'color:{pal["text"]}; background-color:{pal["field"]};">'
                f'&nbsp;{_esc(span.text)}&nbsp;</span>')
        elif span.style == 'bold':
            parts.append(f'<b style="color:{pal["text"]};">{_esc(span.text)}</b>')
        elif span.style == 'link':
            parts.append(f'<a href="{_esc(span.href)}">{_esc(span.text)}</a>')
        else:
            parts.append(_esc(span.text))
    return ''.join(parts)


def render_qt(blocks: Tuple[Block, ...], parent=None):
    """Render parsed blocks into a styled QTextDocument for the Help pane

    Styled from src.ui.tokens (palette, UI and mono fonts). Every `##` heading
    carries an `<a name="...">` anchor from heading_anchors(), so
    QTextBrowser.scrollToAnchor(anchor) lands on it. PySide6 is imported here,
    not at module level, so src.docs stays importable without Qt.

    Args:
        blocks: Parsed topic.
        parent: Optional QObject that owns the document.

    Returns:
        A QTextDocument (a QApplication must exist for font metrics).
    """
    from PySide6.QtGui import QTextDocument
    from src.ui.tokens import PALETTE as pal, Font

    ui_font = Font.ui(14)
    mono = Font.mono(12).family()
    anchors = heading_anchors(blocks)
    out = []
    list_open = None  # 'ul' | 'ol' | None

    def close_list():
        if list_open is not None:
            out.append(f'</{list_open}>')
        return None

    for idx, block in enumerate(blocks):
        if block.kind not in ('bullet', 'number'):
            list_open = close_list()

        if block.kind == 'h1':
            out.append(f'<h1>{_qt_spans(block.spans, pal, mono)}</h1>')
        elif block.kind == 'h2':
            out.append(f'<h2><a name="{_esc(anchors[idx])}"></a>'
                       f'{_qt_spans(block.spans, pal, mono)}</h2>')
        elif block.kind == 'h3':
            out.append(f'<h3>{_qt_spans(block.spans, pal, mono)}</h3>')
        elif block.kind == 'p':
            out.append(f'<p>{_qt_spans(block.spans, pal, mono)}</p>')
        elif block.kind in ('bullet', 'number'):
            tag = 'ul' if block.kind == 'bullet' else 'ol'
            if list_open != tag:
                list_open = close_list()
                out.append(f'<{tag}>')
                list_open = tag
            out.append(f'<li>{_qt_spans(block.spans, pal, mono)}</li>')
        elif block.kind == 'code':
            lines = _esc(block.text).replace(' ', '&nbsp;').replace('\n', '<br>')
            out.append(
                f'<table width="100%" cellspacing="0" cellpadding="10" border="1" '
                f'style="border-color:{pal["border"]}; border-style:solid; '
                f'background-color:{pal["console"]};"><tr><td>'
                f'<span style="font-family:\'{mono}\'; font-size:12px; '
                f'color:{pal["text_2"]};">{lines}</span></td></tr></table><p></p>')
        elif block.kind == 'rule':
            out.append('<hr>')
        elif block.kind == 'table':
            out.append(
                f'<table width="100%" cellspacing="0" cellpadding="7" border="1" '
                f'style="border-color:{pal["border"]}; border-style:solid;">')
            for r, row in enumerate(block.rows):
                out.append('<tr>')
                for c, cell in enumerate(row):
                    align = block.aligns[c] if c < len(block.aligns) else 'left'
                    if r == 0:
                        bg, tag = pal['header'], 'th'
                    else:
                        bg, tag = (pal['row_alt'] if r % 2 == 0 else pal['bg_window']), 'td'
                    out.append(
                        f'<{tag} align="{align}" bgcolor="{bg}" valign="top">'
                        f'{_qt_spans(cell, pal, mono)}</{tag}>')
                out.append('</tr>')
            out.append('</table><p></p>')
    close_list()

    sheet = f"""
        body {{ color:{pal['text_2']}; }}
        h1 {{ color:{pal['text']}; font-size:26px; font-weight:500; margin-top:4px; margin-bottom:14px; }}
        h2 {{ color:{pal['text']}; font-size:18px; font-weight:500; margin-top:26px; margin-bottom:8px; }}
        h3 {{ color:{pal['text']}; font-size:15px; font-weight:500; margin-top:18px; margin-bottom:6px; }}
        p, li {{ line-height:150%; }}
        th {{ color:{pal['text']}; font-weight:500; }}
        a {{ color:{pal['accent']}; text-decoration:none; }}
        hr {{ background-color:{pal['border']}; }}
    """
    doc = QTextDocument(parent)
    doc.setDefaultFont(ui_font)
    doc.setDocumentMargin(0)
    doc.setDefaultStyleSheet(sheet)
    doc.setHtml('<body>' + '\n'.join(out) + '</body>')
    return doc


_HTML_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f5f5f7; --fg: #1c1c1e; --muted: #6e6e73; --card: #ffffff;
  --border: #d8d8dc; --accent: #cc4a1f; --code-bg: #eeeef0; --link: #0a58ca;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1c1c1e; --fg: #f2f2f7; --muted: #9a9aa0; --card: #232326;
    --border: #38383c; --accent: #ff8a5c; --code-bg: #2c2c2e; --link: #6aa9ff;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
  line-height: 1.55;
}
.layout { display: flex; min-height: 100vh; }
nav {
  flex: 0 0 240px; padding: 24px 16px; border-right: 1px solid var(--border);
  position: sticky; top: 0; align-self: flex-start; height: 100vh; overflow-y: auto;
}
nav .group-label {
  font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted); margin: 18px 0 6px; padding: 0 10px;
}
nav .group-label:first-child { margin-top: 0; }
nav a {
  display: block; padding: 6px 10px; border-radius: 6px; color: var(--fg);
  text-decoration: none; font-size: 0.92rem;
}
nav a:hover { background: var(--code-bg); }
main { flex: 1; max-width: 46rem; padding: 40px 32px 80px; }
section { margin-bottom: 56px; }
section:first-of-type h1 { margin-top: 0; }
h1 { font-size: 1.9rem; margin: 0.2em 0 0.5em; }
h2 { font-size: 1.35rem; margin: 1.4em 0 0.5em; border-top: 1px solid var(--border); padding-top: 0.8em; }
h3 { font-size: 1.08rem; margin: 1.2em 0 0.4em; }
p { margin: 0.7em 0; }
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: var(--code-bg); padding: 0.1em 0.35em; border-radius: 4px; font-size: 0.9em;
}
pre {
  background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 16px; overflow-x: auto;
}
pre code { background: none; padding: 0; }
a { color: var(--link); }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.92rem; }
th, td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: var(--code-bg); }
hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
ul, ol { padding-left: 1.4em; }
li { margin: 0.3em 0; }
footer { color: var(--muted); font-size: 0.8rem; padding: 24px 32px 60px; max-width: 46rem; }
@media print {
  nav { display: none; }
  main { max-width: none; padding: 0; }
}
@media (max-width: 760px) {
  .layout { flex-direction: column; }
  nav { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--border); }
}
"""


def app_version() -> str:
    """Best-effort app version: Info.plist when frozen, else pyproject.toml"""
    if getattr(sys, 'frozen', False):
        try:
            plist_path = os.path.join(
                os.path.dirname(os.path.dirname(sys.executable)), 'Info.plist')
            with open(plist_path, 'rb') as f:
                data = plistlib.load(f)
            return str(data.get('CFBundleShortVersionString', ''))
        except Exception:
            return ''
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, 'pyproject.toml'), 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('version'):
                    return stripped.split('"')[1]
    except Exception:
        pass
    return ''


def render_site(topics: Tuple[Topic, ...] = TOPICS) -> str:
    """Render the complete standalone documentation page (all topics, one file)"""
    nav_parts = []
    section_parts = []
    last_group = None

    for topic in topics:
        if topic.group != last_group:
            nav_parts.append(f'<div class="group-label">{_esc(topic.group)}</div>')
            last_group = topic.group
        nav_parts.append(f'<a href="#{topic.slug}">{_esc(topic.label)}</a>')

        blocks = load_topic(topic.slug)
        section_parts.append(f'<section id="{topic.slug}">{render_html(blocks, topic.slug + "--")}</section>')

    version = app_version()
    version_str = f' v{_esc(version)}' if version else ''

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gesture Documentation</title>
<style>{_HTML_STYLE}</style>
</head>
<body>
<div class="layout">
<nav>{''.join(nav_parts)}</nav>
<main>{''.join(section_parts)}
<footer>Gesture{version_str} &middot; generated documentation, not stored in the app bundle.</footer>
</main>
</div>
</body>
</html>
"""


# ============================================================================
# DELIVERY
# ============================================================================
def _writable_docs_dir() -> str:
    """Directory the rendered HTML can be written into

    The bundle is read-only and code-signed; nothing may be written inside
    it. Lives in the app's Application Support folder (see
    src.config.app_support_dir).
    """
    d = os.path.join(app_support_dir(), 'docs')
    os.makedirs(d, exist_ok=True)
    return d


def html_output_path() -> str:
    """Path the rendered documentation HTML is written to"""
    return os.path.join(_writable_docs_dir(), 'Gesture-Documentation.html')


def write_site(path: Optional[str] = None) -> str:
    """Render the full site and write it to disk, returning the path written"""
    target = path or html_output_path()
    os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
    with open(target, 'w', encoding='utf-8') as f:
        f.write(render_site())
    return target


def open_site() -> str:
    """Regenerate the HTML site and open it in the default browser

    Regenerated on every call rather than cached: rendering nine short
    Markdown files takes milliseconds, and doing so guarantees the browser
    copy can never go stale relative to the shipped .md.
    """
    path = write_site()
    webbrowser.open(_path_to_file_uri(path))
    return path


def _path_to_file_uri(path: str) -> str:
    """file:// URI with spaces/special characters correctly percent-encoded"""
    import urllib.parse
    return 'file://' + urllib.parse.quote(os.path.abspath(path))


# ============================================================================
# STANDALONE CHECK
# ============================================================================
if __name__ == '__main__':
    import time

    t0 = time.time()
    for topic in TOPICS:
        blocks = load_topic(topic.slug)
        plan = render_tk(blocks)
        page = render_html(blocks)
        assert plan.chunks, f'{topic.slug}: empty tk render'
        assert page, f'{topic.slug}: empty html render'
    elapsed = time.time() - t0

    site = render_site()
    assert '<html' in site

    print(f'OK: {len(TOPICS)} topics parsed and rendered both ways in {elapsed:.4f}s')
    print('mediapipe imported:', 'mediapipe' in sys.modules)
    print('tkinter imported:', 'tkinter' in sys.modules)
