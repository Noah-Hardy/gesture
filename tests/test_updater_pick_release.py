"""_pick_release: asset regex matching, prerelease filtering, and picking the
newest release regardless of the order GitHub returns the list in."""
from src.updater import _pick_release


def _release(tag, version=None, prerelease=False, draft=False, with_sha=True,
             asset_name=None):
    version = version or tag.lstrip('vV')
    asset_name = asset_name or f'MP-OSC-{version}-macos-arm64.zip'
    assets = [{'name': asset_name, 'browser_download_url': f'https://example/{asset_name}', 'size': 123}]
    if with_sha:
        assets.append({'name': asset_name + '.sha256',
                        'browser_download_url': f'https://example/{asset_name}.sha256'})
    return {
        'tag_name': tag,
        'name': tag,
        'body': '',
        'html_url': f'https://example/{tag}',
        'prerelease': prerelease,
        'draft': draft,
        'assets': assets,
    }


def test_picks_newest_regardless_of_list_order():
    releases = [_release('v0.1.4'), _release('v0.1.6'), _release('v0.1.5')]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked.tag == 'v0.1.6'


def test_skips_prerelease_when_not_included():
    releases = [_release('v0.2.0', prerelease=True), _release('v0.1.6')]
    picked = _pick_release(releases, include_prereleases=False)
    assert picked.tag == 'v0.1.6'


def test_includes_prerelease_when_requested_and_it_is_newest():
    # Release assets are always named with a bare numeric version (the
    # workflow's own asset regex enforces this - see release.yml), so a
    # prerelease is marked via GitHub's `prerelease` flag on an otherwise
    # plain tag, not a "-rc.1"-style tag suffix.
    releases = [_release('v0.1.6'), _release('v0.2.0', prerelease=True)]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked.tag == 'v0.2.0'


def test_skips_drafts():
    releases = [_release('v0.1.7', draft=True), _release('v0.1.6')]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked.tag == 'v0.1.6'


def test_skips_release_with_no_matching_asset():
    releases = [_release('v0.1.7', asset_name='MP-OSC-0.1.7-windows-x64.zip'), _release('v0.1.6')]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked.tag == 'v0.1.6'


def test_skips_release_whose_asset_version_does_not_match_tag():
    # Asset filename version must match the tag - guards against a
    # mismatched/stale asset attached to the wrong release.
    releases = [_release('v0.1.7', asset_name='MP-OSC-0.1.6-macos-arm64.zip')]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked is None


def test_skips_release_missing_sha256_sibling():
    releases = [_release('v0.1.7', with_sha=False), _release('v0.1.6')]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked.tag == 'v0.1.6'


def test_accepts_four_component_hotfix_version():
    releases = [_release('v0.1.5.1')]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked is not None
    assert picked.version == '0.1.5.1'


def test_returns_none_when_no_releases_qualify():
    assert _pick_release([], include_prereleases=True) is None
    assert _pick_release([{'draft': True}], include_prereleases=True) is None


def test_ignores_non_dict_entries():
    releases = [None, 'garbage', _release('v0.1.6')]
    picked = _pick_release(releases, include_prereleases=True)
    assert picked.tag == 'v0.1.6'
