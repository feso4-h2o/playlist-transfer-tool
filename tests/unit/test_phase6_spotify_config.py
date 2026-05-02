from __future__ import annotations

import json

from playlist_porter.config import DEFAULT_SPOTIFY_SCOPES, SpotifyConfig, load_config


def test_spotify_config_loads_env_placeholders_without_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    config_path = tmp_path / "porter.json"
    config_path.write_text(
        json.dumps(
            {
                "database_path": "state/playlist.sqlite",
                "mock": {
                    "source_playlists_path": "fixtures/playlists.json",
                    "destination_catalog_path": "fixtures/catalog.json",
                },
                "spotify": {
                    "client_id": "${SPOTIFY_CLIENT_ID}",
                    "client_secret": "${SPOTIFY_CLIENT_SECRET}",
                    "redirect_uri": "http://127.0.0.1:8080/callback",
                    "scopes": "playlist-read-private playlist-modify-private",
                    "cache_path": "state/spotify-token-cache",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.spotify is not None
    assert config.spotify.client_id is None
    assert config.spotify.redirect_uri == "http://127.0.0.1:8080/callback"
    assert config.spotify.scopes == ("playlist-read-private", "playlist-modify-private")
    assert config.spotify.cache_path == tmp_path / "state" / "spotify-token-cache"


def test_spotify_config_from_env_uses_default_scopes(monkeypatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost/callback")
    monkeypatch.delenv("SPOTIFY_SCOPES", raising=False)

    config = SpotifyConfig.from_env()

    assert config.missing_credentials() == ()
    assert config.scopes == DEFAULT_SPOTIFY_SCOPES
