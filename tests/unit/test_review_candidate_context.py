from __future__ import annotations

from datetime import date
from io import StringIO

from rich.console import Console

from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import MatchDecision, TrackCandidate, UniversalTrack
from playlist_porter.review.terminal import _render_decision


def _track(
    title: str,
    *,
    artist: str = "Artist",
    platform: str | None = "spotify",
    track_id: str | None = "dest-1",
    album: str | None = "Album",
    isrc: str | None = "USRC17607839",
    duration: int | None = 181,
    release_date: date | None = date(2020, 5, 1),
    release_year: int | None = None,
    explicit: bool | None = True,
) -> UniversalTrack:
    return UniversalTrack(
        title=title,
        artists=[artist],
        platform=platform,
        platform_track_id=track_id,
        album=album,
        isrc=isrc,
        duration_seconds=duration,
        release_date=release_date,
        release_year=release_year,
        explicit=explicit,
    )


def _candidate(track: UniversalTrack, *, rank: int, score: float) -> TrackCandidate:
    return TrackCandidate(
        track=track,
        rank=rank,
        score=score,
        evidence={"reason_codes": "duration_mismatch" if rank == 2 else None},
    )


def _render_text(decision: MatchDecision) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=220)
    _render_decision(console, decision)
    return output.getvalue()


def test_review_output_renders_ambiguity_reason_and_candidate_metadata() -> None:
    source = _track("Source Song", track_id="source-1")
    first = _candidate(
        _track("Destination Song", track_id="spotify-track-id"),
        rank=1,
        score=0.9321,
    )
    second = _candidate(
        _track(
            "Destination Song Alternate",
            track_id="spotify-alt-id",
            album="Single",
            release_date=None,
            release_year=2021,
            explicit=False,
        ),
        rank=2,
        score=0.9121,
    )
    decision = MatchDecision(
        source_track=source,
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[first, second],
        score=first.score,
        evidence=first.evidence,
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "ambiguous_candidates" in text
    assert "Destination Song" in text
    assert "album=Album" in text
    assert "duration=3:01" in text
    assert "release=2020-05-01" in text
    assert "release=2021" in text
    assert "explicit=yes" in text
    assert "explicit=no" in text
    assert "isrc=USRC17607839" in text
    assert "id=spotify-track-id" in text
    assert "delta=0.0200" in text
    assert "duration_mismatch" in text
    assert "https://open.spotify.com/track/spotify-track-id" in text


def test_review_output_derives_qqmusic_songmid_url() -> None:
    decision = MatchDecision(
        source_track=_track("Source", platform="qqmusic", track_id="source-mid"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track("Destination", platform="qqmusic", track_id="qqsongmid"),
                rank=1,
                score=0.9,
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "https://y.qq.com/n/ryqq/songDetail/qqsongmid" in text


def test_review_output_omits_missing_optional_metadata_without_none_text() -> None:
    decision = MatchDecision(
        source_track=_track("Source"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track(
                    "Sparse",
                    platform="mock",
                    track_id=None,
                    album=None,
                    isrc=None,
                    duration=None,
                    release_date=None,
                    release_year=None,
                    explicit=None,
                ),
                rank=1,
                score=0.8,
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "Sparse" in text
    assert "None" not in text
    assert "url=" not in text
