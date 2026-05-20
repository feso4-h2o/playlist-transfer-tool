from __future__ import annotations

import json

import pytest

from playlist_porter.config import (
    DEFAULT_SPOTIFY_SCOPES,
    SpotifyConfig,
    default_config_payload,
    load_config,
)


def test_spotify_config_uses_env_credentials_and_json_behavior(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env-client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost/callback")
    monkeypatch.setenv("SPOTIFY_SCOPES", "playlist-read-private playlist-modify-private")
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
                    "client_id": "ignored-client",
                    "client_secret": "ignored-secret",
                    "redirect_uri": "http://ignored/callback",
                    "scopes": "ignored-scope",
                    "cache_path": "state/spotify-token-cache",
                    "create_public_playlists": True,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.spotify is not None
    assert config.spotify.client_id == "env-client"
    assert config.spotify.client_secret == "env-secret"
    assert config.spotify.redirect_uri == "http://localhost/callback"
    assert config.spotify.scopes == ("playlist-read-private", "playlist-modify-private")
    assert config.spotify.cache_path == tmp_path / "state" / "spotify-token-cache"
    assert config.spotify.create_public_playlists is True


def test_spotify_config_from_env_uses_default_scopes(monkeypatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://localhost/callback")
    monkeypatch.delenv("SPOTIFY_SCOPES", raising=False)

    config = SpotifyConfig.from_env()

    assert config.missing_credentials() == ()
    assert config.scopes == DEFAULT_SPOTIFY_SCOPES


def test_default_config_keeps_credentials_out_of_platform_blocks() -> None:
    payload = default_config_payload()

    assert "client_id" not in payload["spotify"]
    assert "client_secret" not in payload["spotify"]
    assert "redirect_uri" not in payload["spotify"]
    assert "scopes" not in payload["spotify"]
    assert "auth_mode" not in payload["spotify"]
    assert "credential_path" not in payload["qqmusic"]
    assert "credential" not in payload["qqmusic"]
    assert "user_id" not in payload["qqmusic"]
    assert payload["qqmusic"]["allow_anonymous_read"] is True
    assert "transfer" not in payload["commands"]
    assert "execute" not in payload["commands"]
    assert "resume" not in payload["commands"]
    assert "export_report" not in payload["commands"]
    assert payload["source_platform"] == "mock"
    assert payload["destination_platform"] == "mock"
    assert payload["report_format"] == "json"
    assert payload["run_id"] == ""
    assert "dry_run" not in payload["commands"]["match"]
    assert set(payload["commands"]["match"]) == {"source_playlist", "restart"}
    assert payload["commands"]["review"] == {"pending_only": False}
    assert set(payload["commands"]["write"]) == {
        "destination_playlist_id",
        "create_playlist",
        "destination_target_type",
    }
    assert payload["commands"]["write"]["destination_target_type"] == "playlist"


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
                    "cache_path": "state/spotify-token-cache",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.spotify is not None
    assert config.spotify.scopes == DEFAULT_SPOTIFY_SCOPES


def test_spotify_config_ignores_extra_spotify_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback")
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
                    "redirect_uri": "http://127.0.0.1:8080/callback",
                    "unused": "ignored",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.spotify is not None
    assert config.spotify.missing_credentials() == ()


def test_qqmusic_config_uses_env_credentials_and_json_behavior(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QQMUSIC_CREDENTIAL_PATH", "qqmusic-credential.json")
    monkeypatch.setenv("QQMUSIC_USER_ID", "12345")
    config_path = tmp_path / "porter.json"
    config_path.write_text(
        json.dumps(
            {
                "database_path": "state/playlist.sqlite",
                "mock": {
                    "source_playlists_path": "fixtures/playlists.json",
                    "destination_catalog_path": "fixtures/catalog.json",
                },
                "qqmusic": {
                    "credential_path": "ignored-credential.json",
                    "credential": {"uin": "ignored"},
                    "user_id": "ignored-user",
                    "page_size": 50,
                    "supports_create_playlist": False,
                    "supports_add_tracks": False,
                    "allow_anonymous_read": False,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.qqmusic is not None
    assert config.qqmusic.credential_path is not None
    assert str(config.qqmusic.credential_path) == "qqmusic-credential.json"
    assert config.qqmusic.credential_payload is None
    assert config.qqmusic.user_id == "12345"
    assert config.qqmusic.page_size == 50
    assert config.qqmusic.supports_create_playlist is False
    assert config.qqmusic.supports_add_tracks is False
    assert config.qqmusic.allow_anonymous_read is False


def test_command_defaults_load_config_oriented_values(tmp_path) -> None:
    config_path = tmp_path / "porter.json"
    config_path.write_text(
        json.dumps(
            {
                "database_path": "state/playlist.sqlite",
                "source_platform": "spotify",
                "destination_platform": "mock",
                "run_id": "review-run",
                "report_format": "both",
                "mock": {
                    "source_playlists_path": "fixtures/playlists.json",
                    "destination_catalog_path": "fixtures/catalog.json",
                },
                "commands": {
                    "match": {
                        "source_playlist": "playlist-url",
                        "restart": True,
                    },
                    "review": {
                        "pending_only": True,
                    },
                    "write": {
                        "destination_playlist_id": "dest",
                        "create_playlist": "Copy",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.database_path == tmp_path / "state" / "playlist.sqlite"
    assert config.source_platform == "spotify"
    assert config.destination_platform == "mock"
    assert config.run_id == "review-run"
    assert config.report_format == "both"
    assert config.commands.match.source_playlist == "playlist-url"
    assert config.commands.match.restart is True
    assert config.commands.review.pending_only is True
    assert config.commands.write.destination_playlist_id == "dest"
    assert config.commands.write.create_playlist == "Copy"
    assert config.commands.write.destination_target_type == "playlist"


def test_write_command_loads_liked_songs_target_type(tmp_path) -> None:
    config_path = tmp_path / "porter.json"
    config_path.write_text(
        json.dumps(
            {
                "database_path": "state/playlist.sqlite",
                "mock": {
                    "source_playlists_path": "fixtures/playlists.json",
                    "destination_catalog_path": "fixtures/catalog.json",
                },
                "commands": {
                    "write": {
                        "destination_target_type": "liked_songs",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.commands.write.destination_target_type == "liked_songs"


def test_invalid_write_target_type_fails_clearly(tmp_path) -> None:
    config_path = tmp_path / "porter.json"
    config_path.write_text(
        json.dumps(
            {
                "database_path": "state/playlist.sqlite",
                "mock": {
                    "source_playlists_path": "fixtures/playlists.json",
                    "destination_catalog_path": "fixtures/catalog.json",
                },
                "commands": {
                    "write": {
                        "destination_target_type": "albums",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="destination_target_type"):
        load_config(config_path)


def test_empty_report_format_defaults_to_json(tmp_path) -> None:
    config_path = tmp_path / "porter.json"
    config_path.write_text(
        json.dumps(
            {
                "database_path": "state/playlist.sqlite",
                "report_format": "",
                "mock": {
                    "source_playlists_path": "fixtures/playlists.json",
                    "destination_catalog_path": "fixtures/catalog.json",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.report_format == "json"
