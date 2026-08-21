"""
docs.py parser/renderer: reuses the same golden checks as the module's own
`python -m src.docs` self-check (every topic parses and renders non-empty
both ways), turning that manual check into something CI enforces.
"""
from src.docs import TOPICS, load_topic, render_html, render_site, render_tk


def test_every_topic_parses_and_renders_both_ways():
    for topic in TOPICS:
        blocks = load_topic(topic.slug)
        plan = render_tk(blocks)
        page = render_html(blocks)
        assert plan.chunks, f'{topic.slug}: empty tk render'
        assert page, f'{topic.slug}: empty html render'


def test_render_site_produces_a_full_html_document():
    site = render_site()
    assert '<html' in site


def test_topics_have_unique_slugs():
    slugs = [t.slug for t in TOPICS]
    assert len(slugs) == len(set(slugs))
