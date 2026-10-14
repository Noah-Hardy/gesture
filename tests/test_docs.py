"""
docs.py parser/renderer: reuses the same golden checks as the module's own
`python -m src.docs` self-check (every topic parses and renders non-empty), turning that manual check into
something CI enforces.
"""
from src.docs import TOPICS, load_topic, render_html, render_site


def test_every_topic_parses_and_renders_html():
    for topic in TOPICS:
        blocks = load_topic(topic.slug)
        page = render_html(blocks)
        assert page, f'{topic.slug}: empty html render'


def test_render_site_produces_a_full_html_document():
    site = render_site()
    assert '<html' in site


def test_topics_have_unique_slugs():
    slugs = [t.slug for t in TOPICS]
    assert len(slugs) == len(set(slugs))


# ============================================================================
# ANCHORS (shared by render_html and render_qt)
# ============================================================================
import os
import re

import pytest

from src.docs import heading_anchors, heading_slug, parse


def test_heading_slug_rule():
    assert heading_slug('Show preview window') == 'show-preview-window'
    assert heading_slug('Max/MSP and Max for Live') == 'max-msp-and-max-for-live'
    assert heading_slug('`json` and the network packet size') == 'json-and-the-network-packet-size'
    assert heading_slug('!!!') == 'section'


def test_duplicate_h2_anchors_get_suffixes():
    anchors = heading_anchors(parse('## A\n\ntext\n\n## A\n\n## B\n'))
    assert list(anchors.values()) == ['a', 'a-2', 'b']


def test_html_h2_ids_match_anchors():
    for topic in TOPICS:
        blocks = load_topic(topic.slug)
        page = render_html(blocks, 'x--')
        for anchor in heading_anchors(blocks).values():
            assert f'id="x--{anchor}"' in page
    assert 'id="quick-start--first-run"' in render_site()


# ============================================================================
# QT RENDERER / HELP VIEW
# ============================================================================
@pytest.fixture(scope='module')
def qapp():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_render_qt_every_topic(qapp):
    from src.docs import render_qt
    for topic in TOPICS:
        doc = render_qt(load_topic(topic.slug))
        assert doc.toPlainText().strip(), f'{topic.slug}: empty qt render'


def test_render_qt_anchors_exist_for_every_h2(qapp):
    from src.docs import render_qt
    for topic in TOPICS:
        blocks = load_topic(topic.slug)
        html = render_qt(blocks).toHtml()
        for anchor in heading_anchors(blocks).values():
            assert re.search(rf'name="{re.escape(anchor)}"', html), (topic.slug, anchor)


def test_render_qt_styles_tables_code_bold_links(qapp):
    from src.docs import render_qt
    md = ('Hello **bold** `code` [site](https://example.com)\n\n'
          '```\nx = 1\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n')
    doc = render_qt(parse(md))
    html = doc.toHtml()
    assert 'font-weight' in html and 'https://example.com' in html
    assert '<table' in html
    assert 'x = 1' in doc.toPlainText().replace('\xa0', ' ')


def test_render_qt_module_import_is_lazy():
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, '-c', 'import sys, src.docs; print("PySide6" in sys.modules)'],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == 'False'


def test_resolve_help_url():
    from src.ui.help_view import resolve_help_url
    assert resolve_help_url('help://quick-start#first-run') == ('quick-start', 'first-run')
    assert resolve_help_url('help://troubleshooting') == ('troubleshooting', None)
    for bad in ('help://nope#x', 'https://example.com', 'quick-start'):
        with pytest.raises(ValueError):
            resolve_help_url(bad)


def test_help_view_open_scrolls_to_anchor(qapp):
    from PySide6.QtCore import QUrl
    from src.ui.help_view import HelpView
    view = HelpView()
    view.resize(900, 700)
    view.show()
    bar = view._browser.verticalScrollBar()
    view.open('troubleshooting')
    assert view.current_slug == 'troubleshooting' and bar.value() == 0
    view.open('troubleshooting', 'other-issues')
    assert bar.value() > 0
    view.handle_link(QUrl('help://quick-start#first-run'))
    assert view.current_slug == 'quick-start' and bar.value() > 0
    view.open('welcome')
    assert bar.value() == 0


# ============================================================================
# UI LINKS INTO THE DOCS
# ============================================================================
def test_every_fault_card_learn_more_anchor_exists_in_troubleshooting():
    from src.ui.overlays.faults import HELP_SLUGS

    anchors = set(heading_anchors(load_topic('troubleshooting')).values())
    for key, slug in HELP_SLUGS.items():
        assert slug in anchors, f'fault card {key!r} links to missing troubleshooting#{slug}'


def test_no_stale_launcher_era_statements():
    for topic in TOPICS:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', topic.filename)
        text = open(path, encoding='utf-8').read().lower()
        assert 'until 0.4.0' not in text, topic.filename
        assert 'show preview window' not in text, topic.filename
