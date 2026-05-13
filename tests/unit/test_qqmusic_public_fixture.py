from pathlib import Path

from playlist_porter.platforms.mock import MockAdapter


def test_qqmusic_public_playlist_fixture_loads_as_mock_source() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    adapter = MockAdapter.from_json(
        playlists_path=repo_root / "fixtures" / "qqmusic-public-playlist.json",
        catalog_path=repo_root / "fixtures" / "mock-catalog.json",
    )

    playlist = adapter.get_playlist("qqmusic-public-sample")

    assert playlist.platform == "qqmusic"
    assert playlist.platform_playlist_id == "qqmusic-public-sample"
    assert playlist.name == "QQ Music Public Sample Playlist"
    assert len(playlist.tracks) == 49

    first_track = playlist.tracks[0]
    assert first_track.platform == "qqmusic"
    assert first_track.platform_track_id == "208426581:1"
    assert first_track.title == "I Did Something Bad"
    assert first_track.artists == ["Taylor Swift"]
    assert first_track.album == "reputation"
    assert first_track.duration_seconds == 238
    assert first_track.release_year == 2017
    assert first_track.source_playlist_position == 1

    assert playlist.tracks[-1].source_playlist_position == 49
