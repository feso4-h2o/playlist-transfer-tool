from uuid import UUID

import pytest
from pydantic import ValidationError

from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import (
    MatchDecision,
    Playlist,
    TrackCandidate,
    TransferRun,
    UniversalTrack,
)


def test_match_status_values_cover_phase_1_contract() -> None:
    assert {status.value for status in MatchStatus} == {
        "isrc_exact",
        "metadata_high_confidence",
        "metadata_medium_confidence",
        "needs_review",
        "not_found",
        "user_approved",
        "user_rejected",
    }


def test_unavailable_reason_values_cover_phase_1_contract() -> None:
    assert {reason.value for reason in UnavailableReason} == {
        "no_candidates",
        "low_confidence",
        "duration_mismatch",
        "version_mismatch",
        "artist_mismatch",
        "region_unavailable",
    }


def test_universal_track_validates_and_exposes_fingerprint() -> None:
    track = UniversalTrack(
        title="  Test Song ",
        artists=[" Primary Artist ", "Featured Artist"],
        platform=" spotify ",
        platform_track_id=" abc123 ",
        duration_seconds=180,
    )

    assert isinstance(track.internal_id, UUID)
    assert track.title == "Test Song"
    assert track.artists == ["Primary Artist", "Featured Artist"]
    assert track.primary_artist == "Primary Artist"
    assert len(track.track_fingerprint) == 64


def test_universal_track_rejects_empty_title_and_artists() -> None:
    with pytest.raises(ValidationError):
        UniversalTrack(title=" ", artists=["Artist"])

    with pytest.raises(ValidationError):
        UniversalTrack(title="Song", artists=[" "])


def test_playlist_and_transfer_run_models_accept_minimal_valid_data() -> None:
    track = UniversalTrack(title="Song", artists=["Artist"])
    playlist = Playlist(name="Source", platform="qqmusic", tracks=[track])
    transfer_run = TransferRun(
        source_platform="qqmusic",
        destination_platform="spotify",
        source_playlist=playlist,
    )

    assert transfer_run.source_playlist == playlist
    assert transfer_run.dry_run is True
    assert transfer_run.decisions == []


def test_match_decision_records_candidate_evidence_and_reason_codes() -> None:
    source = UniversalTrack(title="Song", artists=["Artist"])
    destination = UniversalTrack(title="Song", artists=["Artist"], platform_track_id="dest-1")
    candidate = TrackCandidate(
        track=destination,
        score=0.91,
        rank=1,
        query="song artist",
        evidence={"duration_delta_seconds": 1},
    )

    decision = MatchDecision(
        source_track=source,
        status=MatchStatus.METADATA_HIGH_CONFIDENCE,
        selected_candidate=candidate,
        score=0.91,
        evidence={"title_score": 1.0},
    )

    assert decision.candidates == [candidate]
    assert decision.selected_candidate == candidate
    assert decision.reason_codes == []


def test_fingerprint_collision_prone_metadata_does_not_auto_confirm_identity() -> None:
    source = UniversalTrack(title="Song (Live)", artists=["Artist"], duration_seconds=180)
    wrong_recording = UniversalTrack(title="Song", artists=["Artist"], duration_seconds=240)
    candidate = TrackCandidate(
        track=wrong_recording,
        score=0.25,
        rank=1,
        evidence={"duration_delta_seconds": 60},
    )

    decision = MatchDecision(
        source_track=source,
        candidates=[candidate],
        status=MatchStatus.NEEDS_REVIEW,
        reason_codes=[UnavailableReason.DURATION_MISMATCH],
    )

    assert source.track_fingerprint == wrong_recording.track_fingerprint
    assert decision.status is MatchStatus.NEEDS_REVIEW
    assert MatchStatus.ISRC_EXACT.value != "track_fingerprint"
