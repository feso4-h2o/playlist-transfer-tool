import json
from pathlib import Path

from playlist_porter.platforms.mock import MockAdapter


def test_spotify_search_catalog_fixture_loads_as_mock_destination() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    catalog_path = repo_root / "fixtures" / "spotify-search-catalog.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert len(payload["catalog"]) == 226
    assert len({track["platform_track_id"] for track in payload["catalog"]}) == 226

    dancing_with_a_stranger = next(
        track
        for track in payload["catalog"]
        if track["platform_track_id"] == "6Qs4SXO9dwPj5GKvVOv8Ki"
    )
    assert dancing_with_a_stranger["platform"] == "spotify"
    assert dancing_with_a_stranger["title"] == "Dancing With A Stranger (with Normani)"
    assert dancing_with_a_stranger["artists"] == ["Sam Smith", "Normani"]
    assert dancing_with_a_stranger["album"] == "Dancing With A Stranger (with Normani)"
    assert dancing_with_a_stranger["isrc"] == "GBUM71807386"

    adapter = MockAdapter.from_json(
        playlists_path=repo_root / "fixtures" / "qqmusic-public-playlist.json",
        catalog_path=catalog_path,
    )

    candidates = adapter.search_tracks("dancing with a stranger sam smith")

    assert candidates
    assert candidates[0].track.platform == "spotify"
    assert any(
        candidate.track.platform_track_id == "6Qs4SXO9dwPj5GKvVOv8Ki"
        for candidate in candidates
    )
