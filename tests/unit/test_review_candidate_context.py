from __future__ import annotations

from datetime import date
from io import StringIO

from rich.console import Console

from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import MatchDecision, TrackCandidate, UniversalTrack
from playlist_porter.persistence.repositories import UserOverride
from playlist_porter.review.terminal import (
    _candidate_ids,
    _candidate_metadata,
    _candidate_row_style,
    _render_decision,
    _source_id_fields,
)


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
    source_position: int | None = None,
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
        source_playlist_position=source_position,
    )


def _candidate(
    track: UniversalTrack,
    *,
    rank: int,
    score: float,
    evidence: dict[str, str | None] | None = None,
) -> TrackCandidate:
    candidate_evidence = {"reason_codes": "duration_mismatch" if rank == 2 else None}
    if evidence:
        candidate_evidence.update(evidence)
    return TrackCandidate(
        track=track,
        rank=rank,
        score=score,
        evidence=candidate_evidence,
    )


def _render_text(decision: MatchDecision, *, override: UserOverride | None = None) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=220)
    _render_decision(console, decision, override=override)
    return output.getvalue()


def test_review_output_renders_ambiguity_reason_and_candidate_metadata() -> None:
    source = _track(
        "Source Song",
        track_id="source-1",
        album="Source Album",
        duration=238,
        source_position=4,
    )
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
    assert "Source Album" in text
    assert "Position: 4" in text
    assert (
        "Album: Source Album | Duration: 3:58 | Release: 2020-05-01 | Explicit: yes"
        in text
    )
    assert "ISRC: USRC17607839 | Platform ID: source-1 | URL: Link | Position: 4" in text
    assert "Destination Song" in text
    assert "Album: Album" in text
    assert "Duration: 3:01" in text
    assert "Duration: 3:58" in text
    assert "Release: 2020-05-01" in text
    assert "Release: 2021" in text
    assert "Explicit: yes" in text
    assert "Explicit: no" in text
    assert "ISRC: USRC17607839" in text
    assert "Platform ID: spotify-track-id" in text
    assert "delta=" not in text
    assert "duration_mismatch" in text
    assert "Link" in text
    assert "https://open.spotify.com/track/spotify-track-id" not in text
    assert "Current decision: none" in text
    assert text.count("├") >= 2


def test_review_output_renders_approved_current_decision() -> None:
    candidate = _candidate(
        _track("Destination Song", track_id="spotify-track-id"),
        rank=1,
        score=0.9321,
    )
    decision = MatchDecision(
        source_track=_track("Source Song", track_id="source-1"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[candidate],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )
    override = UserOverride(
        source_track_internal_id=str(decision.source_track.internal_id),
        status=MatchStatus.USER_APPROVED,
        selected_candidate_internal_id=str(candidate.track.internal_id),
    )

    text = _render_text(decision, override=override)

    assert "Current decision: approved candidate rank 1" in text
    assert _candidate_row_style(candidate, override) == "bold green"


def test_review_output_renders_rejected_current_decision() -> None:
    decision = MatchDecision(
        source_track=_track("Source Song", track_id="source-1"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track("Destination Song", track_id="spotify-track-id"),
                rank=1,
                score=0.9321,
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )
    override = UserOverride(
        source_track_internal_id=str(decision.source_track.internal_id),
        status=MatchStatus.USER_REJECTED,
    )

    text = _render_text(decision, override=override)

    assert "Current decision: rejected" in text


def test_review_output_renders_missing_approved_candidate() -> None:
    candidate = _candidate(
        _track("Destination Song", track_id="spotify-track-id"),
        rank=1,
        score=0.9321,
    )
    decision = MatchDecision(
        source_track=_track("Source Song", track_id="source-1"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[candidate],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )
    override = UserOverride(
        source_track_internal_id=str(decision.source_track.internal_id),
        status=MatchStatus.USER_APPROVED,
        selected_candidate_internal_id="missing-candidate",
    )

    text = _render_text(decision, override=override)

    assert "Current decision: approved candidate unavailable" in text
    assert _candidate_row_style(candidate, override) is None


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

    assert "Link" in text
    assert "https://y.qq.com/n/ryqq/songDetail/qqsongmid" not in text


def test_review_output_uses_qqmusic_url_evidence_for_numeric_track_id() -> None:
    decision = MatchDecision(
        source_track=_track("Source", platform="mock", track_id="source-id"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track("Destination", platform="qqmusic", track_id="200030089:1"),
                rank=1,
                score=0.9,
                evidence={
                    "qqmusic_url": "https://y.qq.com/n/ryqq/songDetail/001abcDEFghi",
                },
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "Platform ID: 200030089:1" in text
    assert "Link" in text
    assert "https://y.qq.com/n/ryqq/songDetail/001abcDEFghi" not in text


def test_review_output_derives_qqmusic_url_from_songmid_evidence() -> None:
    decision = MatchDecision(
        source_track=_track("Source", platform="spotify", track_id="source-id"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track("Destination", platform="qqmusic", track_id="200030089:1"),
                rank=1,
                score=0.9,
                evidence={"qqmusic_songmid": "001abcDEFghi"},
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "Platform ID: 200030089:1" in text
    assert "Link" in text
    assert "https://y.qq.com/n/ryqq/songDetail/001abcDEFghi" not in text


def test_review_output_keeps_numeric_qqmusic_track_id_without_link_evidence() -> None:
    decision = MatchDecision(
        source_track=_track("Source", platform="mock", track_id="source-id"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track("Destination", platform="qqmusic", track_id="200030089:1"),
                rank=1,
                score=0.9,
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "Platform ID: 200030089:1" in text
    assert "URL: -" in text


def test_review_output_uses_qqmusic_source_url_evidence() -> None:
    decision = MatchDecision(
        source_track=_track("Source", platform="qqmusic", track_id="200030089:1"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track("Destination", platform="spotify", track_id="spotify-track-id"),
                rank=1,
                score=0.9,
            )
        ],
        evidence={
            "source_qqmusic_url": "https://y.qq.com/n/ryqq/songDetail/001abcDEFghi"
        },
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "Platform ID: 200030089:1 | URL: Link" in text
    assert "https://y.qq.com/n/ryqq/songDetail/001abcDEFghi" not in text


def test_review_source_url_ignores_candidate_qqmusic_url_evidence() -> None:
    source = _track("Source", platform="qqmusic", track_id="200030089:1")
    candidate = _candidate(
        _track("Destination", platform="qqmusic", track_id="650091207:1"),
        rank=1,
        score=0.9,
        evidence={"qqmusic_url": "https://y.qq.com/n/ryqq/songDetail/001CandidateMid"},
    )
    source_ids = _source_id_fields(
        source,
        evidence={
            "source_qqmusic_url": "https://y.qq.com/n/ryqq/songDetail/001SourceMid",
            **candidate.evidence,
        },
    )
    candidate_ids = _candidate_ids(candidate)

    assert source_ids.spans[0].style == "link https://y.qq.com/n/ryqq/songDetail/001SourceMid"
    assert (
        candidate_ids.spans[0].style
        == "link https://y.qq.com/n/ryqq/songDetail/001CandidateMid"
    )


def test_review_output_keeps_numeric_qqmusic_source_id_without_url_evidence() -> None:
    decision = MatchDecision(
        source_track=_track("Source", platform="qqmusic", track_id="200030089:1"),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track("Destination", platform="spotify", track_id="spotify-track-id"),
                rank=1,
                score=0.9,
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "Platform ID: 200030089:1 | URL: -" in text


def test_review_output_renders_missing_optional_metadata_as_dash() -> None:
    decision = MatchDecision(
        source_track=_track(
            "Source",
            platform="mock",
            track_id=None,
            album=None,
            isrc=None,
            duration=None,
            release_date=None,
            explicit=None,
        ),
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
    assert "Album: - | Duration: - | Release: - | Explicit: -" in text
    assert "ISRC: - | Platform ID: - | URL: - | Position: -" in text


def test_candidate_metadata_and_ids_render_missing_values_as_dash() -> None:
    candidate = _candidate(
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

    metadata = _candidate_metadata(candidate)
    ids = _candidate_ids(candidate)

    assert getattr(metadata, "plain", metadata) == (
        "Album: -\nDuration: -\nRelease: -\nExplicit: -"
    )
    assert getattr(ids, "plain", ids) == "ISRC: -\nPlatform ID: -\nURL: -"


def test_review_output_escapes_rich_markup_in_metadata_values() -> None:
    decision = MatchDecision(
        source_track=_track(
            "Source",
            platform="mock",
            track_id="source[/oops]",
            album="Source [/oops]",
            isrc="SRC[/oops]",
            duration=180,
        ),
        status=MatchStatus.NEEDS_REVIEW,
        candidates=[
            _candidate(
                _track(
                    "Candidate",
                    platform="mock",
                    track_id="dest[/oops]",
                    album="Candidate [/oops]",
                    isrc="DST[/oops]",
                ),
                rank=1,
                score=0.8,
            )
        ],
        reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
    )

    text = _render_text(decision)

    assert "Source [/oops]" in text
    assert "Platform ID: source[/oops]" in text
    assert "Candidate [/oops]" in text
    assert "ISRC: DST[/oops]" in text
