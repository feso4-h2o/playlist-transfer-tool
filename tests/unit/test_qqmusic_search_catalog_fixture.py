import json
from pathlib import Path

from playlist_porter.platforms.mock import MockAdapter


def test_qqmusic_search_catalog_fixture_loads_as_mock_destination() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    catalog_path = repo_root / "fixtures" / "qqmusic-search-catalog.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert len(payload["catalog"]) == 277
    assert len({track["platform_track_id"] for track in payload["catalog"]}) == 277

    first_steps = next(
        track
        for track in payload["catalog"]
        if track["platform_track_id"] == "213221021:1"
    )
    assert first_steps["platform"] == "qqmusic"
    assert first_steps["title"] == "First Steps"
    assert first_steps["artists"] == ["Lena Raine"]
    assert first_steps["album"] == "Celeste Original Soundtrack"
    assert first_steps["qqmusic_songmid"] == "00404MpZ2OUaEZ"

    adapter = MockAdapter.from_json(
        playlists_path=repo_root / "fixtures" / "spotify-public-playlist.json",
        catalog_path=catalog_path,
    )

    candidates = adapter.search_tracks("first steps lena raine")

    assert candidates
    assert candidates[0].track.platform == "qqmusic"
    assert any(
        candidate.track.platform_track_id == "213221021:1"
        for candidate in candidates
    )
    first_steps_candidate = next(
        candidate
        for candidate in candidates
        if candidate.track.platform_track_id == "213221021:1"
    )
    assert first_steps_candidate.evidence["qqmusic_songmid"] == "00404MpZ2OUaEZ"
    assert (
        first_steps_candidate.evidence["qqmusic_url"]
        == "https://y.qq.com/n/ryqq/songDetail/00404MpZ2OUaEZ"
    )
