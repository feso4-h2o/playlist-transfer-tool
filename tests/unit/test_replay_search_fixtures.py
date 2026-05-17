import json
from pathlib import Path
from typing import Any

import pytest

from playlist_porter.matching.candidates import match_track
from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import MatchDecision, UniversalTrack
from playlist_porter.platforms.mock import MockAdapter


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _replay_fixture_path(name: str) -> Path:
    return _repo_root() / "tests" / "fixtures" / "replay" / name


def _fixture_payload(name: str) -> dict[str, Any]:
    return json.loads(_replay_fixture_path(name).read_text(encoding="utf-8"))


def _result_by_source_title(payload: dict[str, Any], title: str) -> dict[str, Any]:
    return next(
        result for result in payload["results"] if result["source_track"]["title"] == title
    )


def _spotify_replay_adapter() -> MockAdapter:
    root = _repo_root()
    return MockAdapter.from_json(
        playlists_path=root / "fixtures" / "qqmusic-public-playlist.json",
        catalog_path=root / "fixtures" / "spotify-search-catalog.json",
        search_results_path=_replay_fixture_path("spotify-search-results.json"),
    )


def _qqmusic_replay_adapter() -> MockAdapter:
    root = _repo_root()
    return MockAdapter.from_json(
        playlists_path=root / "fixtures" / "spotify-public-playlist.json",
        catalog_path=root / "fixtures" / "qqmusic-search-catalog.json",
        search_results_path=_replay_fixture_path("qqmusic-search-results.json"),
    )


def _decision_from_replay_fixture(fixture_name: str, source_title: str) -> MatchDecision:
    payload = _fixture_payload(fixture_name)
    source_record = _result_by_source_title(payload, source_title)["source_track"]
    source_track = UniversalTrack.model_validate(source_record)
    adapter = (
        _spotify_replay_adapter()
        if fixture_name == "spotify-search-results.json"
        else _qqmusic_replay_adapter()
    )
    return match_track(source_track, adapter)


def test_spotify_replay_search_fixture_returns_historical_exact_query_candidates() -> None:
    adapter = _spotify_replay_adapter()

    candidates = adapter.search_tracks("i did something bad taylor swift", limit=2)

    assert [candidate.rank for candidate in candidates] == [1, 2]
    assert [candidate.score for candidate in candidates] == [0.998, 0.9964]
    assert candidates[0].query == "i did something bad taylor swift"
    assert candidates[0].track.platform == "spotify"
    assert candidates[0].track.platform_track_id == "4svZDCRz4cJoneBpjpx8DJ"
    assert candidates[0].evidence["spotify_search_rank"] == 1
    assert candidates[0].evidence["spotify_uri"] == "spotify:track:4svZDCRz4cJoneBpjpx8DJ"


def test_replay_search_fixture_uses_exact_historical_queries_only() -> None:
    adapter = _spotify_replay_adapter()

    assert adapter.search_tracks("I Did Something Bad Taylor Swift") == []


def test_qqmusic_replay_search_fixture_returns_historical_exact_query_candidates() -> None:
    adapter = _qqmusic_replay_adapter()

    candidates = adapter.search_tracks("prologue lena raine", limit=1)

    assert len(candidates) == 1
    assert candidates[0].rank == 1
    assert candidates[0].score == 0.998
    assert candidates[0].track.platform == "qqmusic"
    assert candidates[0].track.platform_track_id == "213221025:1"
    assert candidates[0].evidence["search_rank"] == 1
    assert candidates[0].evidence["qqmusic_capability"] == "search_by_type"


def test_spotify_replay_fixture_covers_historical_ambiguous_match_regression() -> None:
    payload = _fixture_payload("spotify-search-results.json")
    source_record = _result_by_source_title(payload, "I Did Something Bad")["source_track"]
    source_track = UniversalTrack.model_validate(source_record)
    adapter = _spotify_replay_adapter()

    decision = match_track(source_track, adapter)

    assert decision.status is MatchStatus.NEEDS_REVIEW
    assert decision.score == 0.998
    assert decision.selected_candidate is None
    assert UnavailableReason.AMBIGUOUS_CANDIDATES in decision.reason_codes


def test_spotify_replay_fixture_covers_historical_low_confidence_regression() -> None:
    payload = _fixture_payload("spotify-search-results.json")
    source_record = _result_by_source_title(payload, "Papillon")["source_track"]
    source_track = UniversalTrack.model_validate(source_record)
    adapter = _spotify_replay_adapter()

    decision = match_track(source_track, adapter)

    assert decision.status is MatchStatus.NOT_FOUND
    assert decision.score == 0.658
    assert decision.selected_candidate is None
    assert UnavailableReason.LOW_CONFIDENCE in decision.reason_codes


def test_spotify_replay_fixture_covers_historical_version_mismatch_candidate_reasons() -> None:
    decision = _decision_from_replay_fixture("spotify-search-results.json", "Deja Vu (Explicit)")

    assert decision.status is MatchStatus.NEEDS_REVIEW
    assert UnavailableReason.VERSION_MISMATCH in decision.reason_codes
    assert decision.candidates[0].evidence["reason_codes"] == "version_mismatch"
    assert decision.candidates[4].evidence["reason_codes"] == "artist_mismatch,version_mismatch"


@pytest.mark.parametrize(
    ("source_title", "expected_decision_reasons", "expected_top_candidate_reasons"),
    [
        (
            "\u7f8e\u4eba",
            [
                UnavailableReason.LOW_CONFIDENCE,
                UnavailableReason.DURATION_MISMATCH,
                UnavailableReason.ARTIST_MISMATCH,
            ],
            "artist_mismatch,duration_mismatch",
        ),
        (
            "Barricades (\u67b7\u9501)",
            [UnavailableReason.LOW_CONFIDENCE, UnavailableReason.ARTIST_MISMATCH],
            "artist_mismatch",
        ),
    ],
)
def test_spotify_replay_fixture_covers_non_english_artist_reason_regressions(
    source_title: str,
    expected_decision_reasons: list[UnavailableReason],
    expected_top_candidate_reasons: str,
) -> None:
    decision = _decision_from_replay_fixture("spotify-search-results.json", source_title)

    assert decision.status is MatchStatus.NOT_FOUND
    assert decision.reason_codes == expected_decision_reasons
    assert decision.candidates[0].evidence["reason_codes"] == expected_top_candidate_reasons


def test_qqmusic_replay_fixture_covers_cross_language_ambiguous_title_candidates() -> None:
    decision = _decision_from_replay_fixture(
        "qqmusic-search-results.json",
        "\ub208,\ucf54,\uc785 (Eyes, Nose, Lips)",
    )

    assert decision.status is MatchStatus.NEEDS_REVIEW
    assert decision.reason_codes == [UnavailableReason.AMBIGUOUS_CANDIDATES]
    assert decision.candidates[0].track.platform_track_id == "5665639:1"
    assert decision.candidates[1].track.platform_track_id == "233031271:1"
    assert decision.candidates[3].evidence["reason_codes"] == "duration_mismatch,version_mismatch"


def test_qqmusic_replay_fixture_covers_translated_artist_name_mismatch_candidate() -> None:
    decision = _decision_from_replay_fixture(
        "qqmusic-search-results.json",
        "Dance with STEEL BALL RUN",
    )

    assert decision.status is MatchStatus.NOT_FOUND
    assert decision.reason_codes == [
        UnavailableReason.LOW_CONFIDENCE,
        UnavailableReason.ARTIST_MISMATCH,
    ]
    assert decision.candidates[0].track.platform_track_id == "650091207:1"
    assert decision.candidates[0].evidence["reason_codes"] == "artist_mismatch"
