import pytest

from subtrans.mkv import _classify, pick_source

from .helpers import track

ALL_KINDS = {"text", "pgs", "vobsub"}


def test_portuguese_track_skips() -> None:
    chosen, reason = pick_source([track(2, "eng"), track(3, "por")], ALL_KINDS)
    assert chosen is None
    assert "Portuguese" in reason


def test_forced_portuguese_track_does_not_skip() -> None:
    chosen, _ = pick_source([track(2, "eng"), track(3, "por", forced=True)], ALL_KINDS)
    assert chosen is not None and chosen.id == 2


def test_non_english_only_skips() -> None:
    chosen, reason = pick_source([track(2, "jpn"), track(3, "spa")], ALL_KINDS)
    assert chosen is None
    assert "no full English" in reason


def test_forced_english_is_never_the_source() -> None:
    tracks = [track(2, "eng", forced=True), track(3, "eng", "pgs")]
    chosen, _ = pick_source(tracks, ALL_KINDS)
    assert chosen is not None and chosen.id == 3
    chosen, reason = pick_source([track(2, "eng", forced=True)], ALL_KINDS)
    assert chosen is None
    assert "no full English" in reason


def test_untagged_rip_is_read_as_english() -> None:
    chosen, _ = pick_source([track(2, "und", "pgs"), track(3, "und")], ALL_KINDS)
    assert chosen is not None and chosen.id == 3
    chosen, _ = pick_source([track(2, "und"), track(3, "jpn")], ALL_KINDS)
    assert chosen is None


@pytest.mark.parametrize(
    ("codec", "kind"),
    [
        ("S_TEXT/UTF8", "text"),
        ("S_TEXT/ASS", "text"),
        ("SubRip/SRT", "text"),
        ("SubStationAlpha", "text"),
        ("S_HDMV/PGS", "pgs"),
        ("S_VOBSUB", "vobsub"),
        ("S_DVBSUB", None),
    ],
)
def test_classify(codec: str, kind: str | None) -> None:
    assert _classify(codec) == kind


def test_prefers_english_text_over_pgs() -> None:
    chosen, _ = pick_source([track(2, "eng", "pgs"), track(4, "eng"), track(5, "jpn")], ALL_KINDS)
    assert chosen is not None and chosen.id == 4


def test_unreadable_kind_skips() -> None:
    chosen, reason = pick_source([track(2, "eng", "vobsub")], {"text", "pgs"})
    assert chosen is None
    assert "vobsub" in reason
