"""parse_version / compare_versions: the exact ranking that decides whether
"update available" gets shown. The 0.1.4 update-check bugs lived here."""
from src.updater import compare_versions, parse_version


def test_parse_version_rejects_empty_and_none():
    assert parse_version('') is None
    assert parse_version(None) is None


def test_parse_version_rejects_unparseable():
    assert parse_version('not-a-version') is None


def test_parse_version_accepts_leading_v():
    assert parse_version('v0.1.6') == parse_version('0.1.6')


def test_parse_version_pads_missing_components():
    # A bare "1" and "1.0.0.0" must compare equal
    assert parse_version('1') == parse_version('1.0.0.0')


def test_parse_version_accepts_four_components():
    # Hotfix releases like 0.1.5.1
    assert parse_version('0.1.5.1') is not None


def test_compare_versions_orders_numerically_not_lexically():
    # Lexical comparison would put "0.1.10" before "0.1.9"
    assert compare_versions('0.1.10', '0.1.9') == 1
    assert compare_versions('0.1.9', '0.1.10') == -1


def test_compare_versions_equal():
    assert compare_versions('0.1.6', 'v0.1.6') == 0


def test_compare_versions_prerelease_sorts_below_release():
    assert compare_versions('0.2.0-rc.1', '0.2.0') == -1
    assert compare_versions('0.2.0', '0.2.0-rc.1') == 1


def test_compare_versions_prerelease_ordering_is_lexical_on_suffix():
    # rc.1 < rc.2 because the suffix string itself sorts that way
    assert compare_versions('0.2.0-rc.1', '0.2.0-rc.2') == -1


def test_compare_versions_unparseable_returns_none():
    assert compare_versions('garbage', '0.1.6') is None
    assert compare_versions('0.1.6', 'garbage') is None
