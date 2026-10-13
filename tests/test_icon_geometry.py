"""Tests for the Qt-free icon geometry shared by make_icon and the UI."""

from src import icon_geometry as g


def test_tier_boundaries():
    assert [g.tier_for(s) for s in (1024, 128, 127, 64, 63, 32, 31, 16)] == [
        'full', 'full', 'mid', 'mid', 'small', 'small', 'tiny', 'tiny']


def test_tier_detail():
    assert len(g.TIERS['full'].nodes) == 11 and g.TIERS['full'].circle
    assert len(g.TIERS['tiny'].nodes) == 5
    assert not any(g.TIERS[k].circle for k in ('mid', 'small', 'tiny'))
    assert [g.TIERS[k].scale for k in ('full', 'mid', 'small', 'tiny')] == [1.0, 1.08, 1.12, 1.18]


def test_points_inside_square():
    for tier in g.TIERS.values():
        pts = [p for chain in tier.bones for p in chain] + [p for p, _ in tier.nodes]
        for p in pts:
            x, y = g.scaled_point(p, tier.scale)
            assert 0 <= x <= 100 and 0 <= y <= 100
