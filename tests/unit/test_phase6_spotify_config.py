from __future__ import annotations

import json

from playlist_porter.config import (
    DEFAULT_SPOTIFY_SCOPES,
    SpotifyConfig,
    default_config_payload,
    load_config,
)


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
    assert config.spotify.auth_mode == "auto"


def test_spotify_config_from_env_uses_default_scopes(monkeypatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost/callback")
    monkeypatch.delenv("SPOTIFY_SCOPES", raising=False)

    config = SpotifyConfig.from_env()

    assert config.missing_credentials() == ()
    assert config.scopes == DEFAULT_SPOTIFY_SCOPES


def test_default_config_uses_spotify_scope_environment_placeholder() -> None:
    payload = default_config_payload()

    assert payload["spotify"]["scopes"] == "${SPOTIFY_SCOPES}"
    assert payload["spotify"]["auth_mode"] == "auto"
    assert payload["qqmusic"]["allow_anonymous_read"] is True


def test_spotify_config_expands_scope_environment_placeholder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPOTIFY_SCOPES", "playlist-read-private playlist-modify-public")
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
                    "scopes": "${SPOTIFY_SCOPES}",
                    "cache_path": "state/spotify-token-cache",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.spotify is not None
    assert config.spotify.scopes == ("playlist-read-private", "playlist-modify-public")


def test_unset_spotify_scope_placeholder_uses_default_scopes(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPOTIFY_SCOPES", raising=False)
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
                    "scopes": "${SPOTIFY_SCOPES}",
                    "cache_path": "state/spotify-token-cache",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.spotify is not None
    assert config.spotify.scopes == DEFAULT_SPOTIFY_SCOPES


def test_spotify_config_loads_client_credentials_auth_mode(tmp_path) -> None:
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
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "auth_mode": "client_credentials",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.spotify is not None
    assert config.spotify.auth_mode == "client_credentials"
    assert config.spotify.missing_client_credentials() == ()
