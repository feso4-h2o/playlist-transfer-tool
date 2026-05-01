import pytest
from sqlalchemy import inspect

from playlist_porter.matching.candidates import match_playlist
from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import (
    MatchDecision,
    Playlist,
    TrackCandidate,
    TransferRun,
    UniversalTrack,
)
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.platforms.mock import MockAdapter


def _track(
    title: str,
    artist: str = "Artist",
    *,
    track_id: str | None = None,
    isrc: str | None = None,
    duration: int = 180,
) -> UniversalTrack:
    return UniversalTrack(
        title=title,
        artists=[artist],
        platform="mock",
        platform_track_id=track_id,
        isrc=isrc,
        duration_seconds=duration,
    )


def _run(playlist: Playlist) -> TransferRun:
    return TransferRun(
        source_platform="mock-source",
        destination_platform="mock-destination",
        source_playlist=playlist,
        dry_run=True,
    )


def _repo(tmp_path) -> TransferRepository:
    return TransferRepository(tmp_path / "transfer.sqlite")


def test_database_schema_creation(tmp_path) -> None:
    repo = _repo(tmp_path)

    tables = set(inspect(repo.engine).get_table_names())

    assert {
        "transfer_runs",
        "source_tracks",
        "candidate_tracks",
        "match_decisions",
        "user_overrides",
        "transfer_steps",
        "transfer_metrics",
    }.issubset(tables)


def test_save_load_round_trip_for_tracks_candidates_and_decisions(tmp_path) -> None:
    repo = _repo(tmp_path)
    source = _track("Song", track_id="source-1", isrc="ISRC1")
    destination = _track("Song", track_id="dest-1", isrc="ISRC1")
    candidate = TrackCandidate(
        track=destination,
        score=1.0,
        rank=1,
        query="song artist",
        evidence={"title_score": 1.0},
    )
    playlist = Playlist(name="Source", platform_playlist_id="source-playlist", tracks=[source])
    run_id = repo.create_run(_run(playlist))
    decision = MatchDecision(
        source_track=source,
        status=MatchStatus.ISRC_EXACT,
        candidates=[candidate],
        selected_candidate=candidate,
        score=1.0,
        evidence={"isrc_exact": True},
    )

    repo.save_source_playlist(run_id, playlist)
    repo.save_match_decision(run_id, decision)

    loaded_sources = repo.load_source_tracks(run_id)
    loaded_candidates = repo.load_candidates(run_id, source.internal_id)
    loaded_decisions = repo.load_match_decisions(run_id)

    assert loaded_sources[0].platform_track_id == "source-1"
    assert loaded_candidates[0].track.platform_track_id == "dest-1"
    assert loaded_candidates[0].evidence == {"title_score": 1.0}
    assert loaded_decisions[0].status is MatchStatus.ISRC_EXACT
    assert loaded_decisions[0].selected_candidate is not None
    assert loaded_decisions[0].selected_candidate.track.platform_track_id == "dest-1"


def test_transfer_metrics_are_populated_after_mock_match_run(tmp_path) -> None:
    repo = _repo(tmp_path)
    playlist = Playlist(
        name="Source",
        platform_playlist_id="source-playlist",
        tracks=[
            _track("Exact", track_id="source-1", isrc="ISRC1"),
            _track("Missing", track_id="source-2"),
        ],
    )
    adapter = MockAdapter(catalog=[_track("Exact", track_id="dest-1", isrc="ISRC1")])
    run_id = repo.create_run(_run(playlist))

    repo.save_source_playlist(run_id, playlist)
    repo.save_match_decisions(run_id, match_playlist(playlist, adapter))
    metrics = repo.load_metrics(run_id)

    assert metrics.source_track_count == 2
    assert metrics.candidate_count == 1
    assert metrics.auto_accepted_count == 1
    assert metrics.not_found_count == 1
    assert metrics.status_counts == {
        "isrc_exact": 1,
        "not_found": 1,
    }
    assert metrics.unavailable_reason_counts == {"no_candidates": 1}


def test_decision_save_preserves_existing_source_playlist_positions(tmp_path) -> None:
    repo = _repo(tmp_path)
    first = _track("First", track_id="source-1")
    second = _track("Second", track_id="source-2")
    third = _track("Third", track_id="source-3")
    playlist = Playlist(name="Source", tracks=[first, second, third])
    run_id = repo.create_run(_run(playlist))

    repo.save_source_playlist(run_id, playlist)
    repo.save_match_decision(
        run_id,
        MatchDecision(
            source_track=third,
            status=MatchStatus.NOT_FOUND,
            reason_codes=[UnavailableReason.NO_CANDIDATES],
        ),
    )
    repo.save_match_decision(
        run_id,
        MatchDecision(
            source_track=first,
            status=MatchStatus.NOT_FOUND,
            reason_codes=[UnavailableReason.NO_CANDIDATES],
        ),
    )

    assert [track.platform_track_id for track in repo.load_source_tracks(run_id)] == [
        "source-1",
        "source-2",
        "source-3",
    ]
    loaded_decision_track_ids = [
        decision.source_track.platform_track_id
        for decision in repo.load_match_decisions(run_id)
    ]
    assert loaded_decision_track_ids == [
        "source-1",
        "source-3",
    ]


def test_metrics_update_after_user_review_and_write_progress(tmp_path) -> None:
    repo = _repo(tmp_path)
    approved_source = _track("Approved", track_id="source-1")
    rejected_source = _track("Rejected", track_id="source-2")
    playlist = Playlist(name="Source", tracks=[approved_source, rejected_source])
    run_id = repo.create_run(_run(playlist))
    candidate = TrackCandidate(
        track=_track("Approved", track_id="dest-1"),
        score=0.9,
        rank=1,
    )

    repo.save_source_playlist(run_id, playlist)
    repo.save_user_override(
        run_id,
        approved_source.internal_id,
        status=MatchStatus.USER_APPROVED,
        selected_candidate=candidate,
    )
    repo.save_user_override(
        run_id,
        rejected_source.internal_id,
        status=MatchStatus.USER_REJECTED,
        reason_codes=[UnavailableReason.LOW_CONFIDENCE],
    )
    repo.record_write_success(run_id, approved_source.internal_id, "dest-1")
    repo.record_write_failure(
        run_id,
        rejected_source.internal_id,
        "dest-2",
        error="rate limited",
        retry_count=2,
    )

    metrics = repo.load_metrics(run_id)

    assert metrics.user_approved_count == 1
    assert metrics.user_rejected_count == 1
    assert metrics.write_success_count == 1
    assert metrics.write_failure_count == 1
    assert metrics.retry_count == 2


def test_resume_state_skips_completed_write_steps(tmp_path) -> None:
    repo = _repo(tmp_path)
    source = _track("Song", track_id="source-1")
    playlist = Playlist(name="Source", tracks=[source])
    run_id = repo.create_run(_run(playlist))

    repo.save_source_playlist(run_id, playlist)
    repo.record_write_success(run_id, source.internal_id, "dest-1")
    repo.record_write_success(run_id, source.internal_id, "dest-1")

    resume_state = repo.get_resume_state(run_id)

    assert repo.should_write_track(run_id, source.internal_id, "dest-1") is False
    assert repo.should_write_track(run_id, source.internal_id, "dest-2") is True
    assert repo.pending_write_track_ids(
        run_id,
        ["dest-1", "dest-2"],
        source_track_ids=[source.internal_id, source.internal_id],
    ) == ["dest-2"]
    assert str(source.internal_id) in resume_state.completed_write_source_track_ids
    assert resume_state.completed_destination_track_ids == frozenset({"dest-1"})
    assert repo.load_metrics(run_id).write_success_count == 1


def test_pending_write_filter_keeps_duplicate_destination_tracks_source_aware(tmp_path) -> None:
    repo = _repo(tmp_path)
    first = _track("First", track_id="source-1")
    second = _track("Second", track_id="source-2")
    playlist = Playlist(name="Source", tracks=[first, second])
    run_id = repo.create_run(_run(playlist))

    repo.save_source_playlist(run_id, playlist)
    repo.record_write_success(run_id, first.internal_id, "dest-duplicate")

    assert repo.pending_write_track_ids(
        run_id,
        ["dest-duplicate", "dest-duplicate"],
        source_track_ids=[first.internal_id, second.internal_id],
    ) == ["dest-duplicate"]
    with pytest.raises(ValueError, match="source_track_ids are required"):
        repo.pending_write_track_ids(
            run_id,
            ["dest-duplicate", "dest-duplicate"],
        )


def test_user_override_survives_process_restart(tmp_path) -> None:
    database_path = tmp_path / "transfer.sqlite"
    source = _track("Song", track_id="source-1")
    playlist = Playlist(name="Source", tracks=[source])
    first_repo = TransferRepository(database_path)
    run_id = first_repo.create_run(_run(playlist))

    first_repo.save_source_playlist(run_id, playlist)
    first_repo.save_user_override(
        run_id,
        source.internal_id,
        status=MatchStatus.USER_REJECTED,
        reason_codes=[UnavailableReason.VERSION_MISMATCH],
    )

    second_repo = TransferRepository(database_path)
    override = second_repo.load_user_override(run_id, source.internal_id)

    assert override is not None
    assert override.status is MatchStatus.USER_REJECTED
    assert override.reason_codes == (UnavailableReason.VERSION_MISMATCH,)


def test_get_or_create_run_detects_existing_resume_key(tmp_path) -> None:
    repo = _repo(tmp_path)
    playlist = Playlist(
        name="Source",
        platform_playlist_id="source-playlist",
        tracks=[_track("Song")],
    )

    first_id, first_created = repo.get_or_create_run(_run(playlist))
    second_id, second_created = repo.get_or_create_run(_run(playlist))

    assert first_created is True
    assert second_created is False
    assert second_id == first_id


def test_create_run_allows_explicit_restart_for_same_playlist(tmp_path) -> None:
    repo = _repo(tmp_path)
    playlist = Playlist(
        name="Source",
        platform_playlist_id="source-playlist",
        tracks=[_track("Song")],
    )

    first_id = repo.create_run(_run(playlist))
    second_id = repo.create_run(_run(playlist))

    assert second_id != first_id
