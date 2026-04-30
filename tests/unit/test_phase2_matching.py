import json

from playlist_porter.matching.candidates import build_search_queries, match_playlist, match_track
from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import Playlist, UniversalTrack
from playlist_porter.platforms.mock import MockAdapter


def _track(
    title: str,
    artist: str = "Artist",
    *,
    track_id: str | None = None,
    isrc: str | None = None,
    duration: int = 180,
    album: str | None = None,
) -> UniversalTrack:
    return UniversalTrack(
        title=title,
        artists=[artist],
        platform="mock",
        platform_track_id=track_id,
        isrc=isrc,
        duration_seconds=duration,
        album=album,
    )


def test_exact_isrc_match_is_auto_accepted() -> None:
    source = _track("Song", isrc="USRC17607839", duration=201)
    adapter = MockAdapter(
        catalog=[
            _track("Song - Remastered", track_id="dest-1", isrc="USRC17607839", duration=200),
            _track("Song", track_id="dest-2", duration=260),
        ]
    )

    decision = match_track(source, adapter)

    assert decision.status is MatchStatus.ISRC_EXACT
    assert decision.selected_candidate is not None
    assert decision.selected_candidate.track.platform_track_id == "dest-1"
    assert decision.score == 1.0


def test_same_title_artist_wrong_duration_requires_review() -> None:
    source = _track("Song", duration=180)
    adapter = MockAdapter(catalog=[_track("Song", track_id="dest-1", duration=240)])

    decision = match_track(source, adapter)

    assert decision.status is MatchStatus.NEEDS_REVIEW
    assert decision.selected_candidate is None
    assert UnavailableReason.DURATION_MISMATCH in decision.reason_codes


def test_version_mismatch_is_penalized_and_requires_review() -> None:
    source = _track("Song", duration=180)
    adapter = MockAdapter(catalog=[_track("Song (Remix)", track_id="dest-1", duration=180)])

    decision = match_track(source, adapter)

    assert decision.status is MatchStatus.NEEDS_REVIEW
    assert UnavailableReason.VERSION_MISMATCH in decision.reason_codes
    assert decision.score is not None
    assert decision.score < 0.88


def test_multiple_close_candidates_become_needs_review() -> None:
    source = _track("Song", duration=180)
    adapter = MockAdapter(
        catalog=[
            _track("Song", track_id="dest-1", duration=180),
            _track("Song", track_id="dest-2", duration=181),
        ]
    )

    decision = match_track(source, adapter)

    assert decision.status is MatchStatus.NEEDS_REVIEW
    assert len(decision.candidates) == 2
    assert decision.candidates[0].score - decision.candidates[1].score <= 0.03


def test_missing_destination_track_becomes_not_found_with_reason_code() -> None:
    source = _track("Missing Song", duration=180)
    adapter = MockAdapter(catalog=[])

    decision = match_track(source, adapter)

    assert decision.status is MatchStatus.NOT_FOUND
    assert decision.reason_codes == [UnavailableReason.NO_CANDIDATES]
    assert decision.candidates == []


def test_region_limited_candidate_becomes_not_found_with_region_reason() -> None:
    source = _track("Song", duration=180)
    adapter = MockAdapter(
        catalog_entries=[
            {
                "id": "dest-1",
                "title": "Song",
                "artists": ["Artist"],
                "duration_seconds": 180,
                "unavailable_reason": "region_unavailable",
            }
        ]
    )

    decision = match_track(source, adapter)

    assert decision.status is MatchStatus.NOT_FOUND
    assert decision.reason_codes == [UnavailableReason.REGION_UNAVAILABLE]
    assert decision.candidates[0].unavailable_reason is UnavailableReason.REGION_UNAVAILABLE


def test_simplified_traditional_metadata_scores_as_high_confidence_match() -> None:
    source = _track("\u611b\u6b4c", "\u85dd\u8853\u5bb6", duration=180)
    adapter = MockAdapter(catalog=[_track("\u7231\u6b4c", "\u827a\u672f\u5bb6", track_id="dest-1")])

    decision = match_track(source, adapter)

    assert decision.status is MatchStatus.METADATA_HIGH_CONFIDENCE
    assert decision.selected_candidate is not None
    assert decision.evidence["title_score"] == 1.0
    assert decision.evidence["primary_artist_score"] == 1.0


def test_mock_adapter_loads_json_fixtures_and_records_writes(tmp_path) -> None:
    playlists_path = tmp_path / "playlists.json"
    catalog_path = tmp_path / "catalog.json"
    writes_path = tmp_path / "writes.json"
    playlists_path.write_text(
        json.dumps(
            {
                "playlists": [
                    {
                        "id": "source-1",
                        "name": "Source",
                        "tracks": [
                            {
                                "title": "Song",
                                "artists": ["Artist"],
                                "duration_seconds": 180,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(
            {
                "catalog": [
                    {
                        "id": "dest-1",
                        "title": "Song",
                        "artists": ["Artist"],
                        "duration_seconds": 180,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    adapter = MockAdapter.from_json(
        playlists_path=playlists_path,
        catalog_path=catalog_path,
        writes_path=writes_path,
    )
    playlist = adapter.get_playlist("source-1")
    destination_playlist_id = adapter.create_playlist("Copied", "Fixture write")
    adapter.add_tracks(destination_playlist_id, ["dest-1"])

    assert playlist.name == "Source"
    assert json.loads(writes_path.read_text(encoding="utf-8")) == {
        destination_playlist_id: {
            "description": "Fixture write",
            "name": "Copied",
            "track_ids": ["dest-1"],
        }
    }


def test_mock_adapter_loads_numeric_json_track_ids_as_strings(tmp_path) -> None:
    playlists_path = tmp_path / "numeric-playlists.json"
    catalog_path = tmp_path / "numeric-catalog.json"
    playlists_path.write_text(
        json.dumps(
            {
                "playlists": [
                    {
                        "id": 123,
                        "name": "Source",
                        "tracks": [
                            {
                                "id": 456,
                                "title": "Song",
                                "artists": ["Artist"],
                                "duration_seconds": 180,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(
            {
                "catalog": [
                    {
                        "id": 789,
                        "title": "Song",
                        "artists": ["Artist"],
                        "duration_seconds": 180,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    adapter = MockAdapter.from_json(
        playlists_path=playlists_path,
        catalog_path=catalog_path,
    )
    playlist = adapter.get_playlist("123")
    candidates = adapter.search_tracks("song artist")

    assert playlist.tracks[0].platform_track_id == "456"
    assert candidates[0].track.platform_track_id == "789"


def test_playlist_matching_runs_end_to_end_with_mock_adapter() -> None:
    playlist = Playlist(
        name="Source",
        tracks=[
            _track("Exact", track_id="source-1", isrc="ISRC1"),
            _track("Missing", track_id="source-2"),
        ],
    )
    adapter = MockAdapter(catalog=[_track("Exact", track_id="dest-1", isrc="ISRC1")])

    decisions = match_playlist(playlist, adapter)

    assert [decision.status for decision in decisions] == [
        MatchStatus.ISRC_EXACT,
        MatchStatus.NOT_FOUND,
    ]
    assert decisions[1].reason_codes == [UnavailableReason.NO_CANDIDATES]


def test_search_queries_include_chinese_script_forms_and_version_labels() -> None:
    source = _track("\u611b\u6b4c (Live)", "\u85dd\u8853\u5bb6")

    queries = build_search_queries(source)

    assert any("live" in query for query in queries)
    assert any("\u7231\u6b4c" in query for query in queries)
