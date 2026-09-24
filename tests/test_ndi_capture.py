"""
NDI source selection (#31): exact case-insensitive match first, then a
unique substring, and never a silent fallback to some other source.
Pure logic - no NDI runtime needed.
"""
from types import SimpleNamespace

from src.ndi_capture import select_ndi_source


def sources(*names):
    return [SimpleNamespace(ndi_name=n) for n in names]


def test_no_sources():
    chosen, reason = select_ndi_source([], 'Studio')
    assert chosen is None
    assert reason


def test_no_name_takes_the_first_source():
    found = sources('A (OBS)', 'B (OBS)')
    chosen, _ = select_ndi_source(found, '')
    assert chosen is found[0]


def test_exact_match_is_case_insensitive():
    found = sources('STUDIO (OBS)', 'studio (obs) 2')
    chosen, _ = select_ndi_source(found, 'studio (obs)')
    assert chosen is found[0]


def test_exact_match_beats_a_longer_substring_match():
    # "Studio" is a substring of both - the exact one must win, not
    # whichever happens to be listed first
    found = sources('Studio-2', 'Studio')
    chosen, _ = select_ndi_source(found, 'studio')
    assert chosen is found[1]


def test_unique_substring_is_accepted():
    found = sources('MAC-MINI (OBS Program)', 'LAPTOP (Camera)')
    chosen, _ = select_ndi_source(found, 'obs program')
    assert chosen is found[0]


def test_ambiguous_substring_matches_nothing():
    found = sources('Studio-1', 'Studio-2')
    chosen, reason = select_ndi_source(found, 'Studio')
    assert chosen is None
    assert 'Studio-1' in reason and 'Studio-2' in reason


def test_missing_name_never_falls_back_to_the_first_source():
    found = sources('A (OBS)', 'B (OBS)')
    chosen, reason = select_ndi_source(found, 'Stage Left')
    assert chosen is None
    assert 'Stage Left' in reason
