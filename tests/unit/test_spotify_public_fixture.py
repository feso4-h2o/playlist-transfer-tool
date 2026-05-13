from pathlib import Path

from playlist_porter.platforms.mock import MockAdapter


def test_spotify_public_playlist_fixture_loads_as_mock_source() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    adapter = MockAdapter.from_json(
        playlists_path=repo_root / "fixtures" / "spotify-public-playlist.json",
        catalog_path=repo_root / "fixtures" / "mock-catalog.json",
    )

    playlist = adapter.get_playlist("spotify-public-sample")

    assert playlist.platform == "spotify"
    assert playlist.platform_playlist_id == "spotify-public-sample"
    assert playlist.name == "Spotify Public Sample Playlist"
    assert len(playlist.tracks) == 65

    first_track = playlist.tracks[0]
    assert first_track.platform == "spotify"
    assert first_track.platform_track_id == "7irQdnDBovK2AVSBilasDZ"
    assert first_track.title == "Prologue"
    assert first_track.artists == ["Lena Raine"]
    assert first_track.album == "Celeste (Original Soundtrack)"
    assert first_track.duration_seconds == 68
    assert first_track.isrc == "QZARB1852740"
    assert first_track.explicit is False
    assert first_track.release_year == 2018
    assert first_track.source_playlist_position == 0

    assert playlist.tracks[-1].source_playlist_position == 64
