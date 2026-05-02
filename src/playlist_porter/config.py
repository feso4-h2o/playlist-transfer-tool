"""Local JSON configuration for CLI workflows."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SPOTIFY_SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
)


@dataclass(frozen=True)
class SpotifyConfig:
    """Local Spotify OAuth settings."""

    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    scopes: tuple[str, ...] = DEFAULT_SPOTIFY_SCOPES
    cache_path: Path | None = None
    create_public_playlists: bool = False

    @classmethod
    def from_env(cls) -> SpotifyConfig:
        """Build Spotify configuration from environment variables."""

        scopes = _parse_scopes(os.getenv("SPOTIFY_SCOPES"))
        return cls(
            client_id=_optional_text(os.getenv("SPOTIFY_CLIENT_ID")),
            client_secret=_optional_text(os.getenv("SPOTIFY_CLIENT_SECRET")),
            redirect_uri=_optional_text(os.getenv("SPOTIFY_REDIRECT_URI")),
            scopes=scopes or DEFAULT_SPOTIFY_SCOPES,
            cache_path=_default_spotify_cache_path(),
        )

    @property
    def resolved_cache_path(self) -> Path:
        """Return a token-cache path outside tracked source files by default."""

        return self.cache_path or _default_spotify_cache_path()

    @property
    def scope_string(self) -> str:
        """Return Spotipy's space-delimited scope string."""

        return " ".join(self.scopes)

    def missing_credentials(self) -> tuple[str, ...]:
        """Return required OAuth fields that are not configured."""

        missing: list[str] = []
        if not self.client_id:
            missing.append("client_id")
        if not self.client_secret:
            missing.append("client_secret")
        if not self.redirect_uri:
            missing.append("redirect_uri")
        return tuple(missing)


@dataclass(frozen=True)
class PorterConfig:
    """Resolved configuration for the mock CLI workflow."""

    database_path: Path
    report_output_dir: Path
    mock_source_playlists_path: Path
    mock_destination_catalog_path: Path
    mock_writes_path: Path | None = None
    spotify: SpotifyConfig | None = None


def default_config_payload() -> dict[str, Any]:
    """Return a credential-free starter configuration."""

    return {
        "database_path": "state/playlist-porter.sqlite",
        "report_output_dir": "reports",
        "mock": {
            "source_playlists_path": "fixtures/mock-playlists.json",
            "destination_catalog_path": "fixtures/mock-catalog.json",
            "writes_path": "state/mock-writes.json",
        },
        "spotify": {
            "client_id": "${SPOTIFY_CLIENT_ID}",
            "client_secret": "${SPOTIFY_CLIENT_SECRET}",
            "redirect_uri": "http://127.0.0.1:8080/callback",
            "scopes": list(DEFAULT_SPOTIFY_SCOPES),
            "cache_path": str(_default_spotify_cache_path()),
            "create_public_playlists": False,
        },
    }


def write_default_config(path: str | Path, *, force: bool = False) -> Path:
    """Write a starter JSON config and return its path."""

    target = Path(path)
    if target.exists() and not force:
        raise FileExistsError(f"config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(default_config_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def load_config(path: str | Path) -> PorterConfig:
    """Load and resolve a JSON config relative to its own directory."""

    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    base_dir = config_path.parent
    mock_payload = payload.get("mock", {})
    spotify_payload = payload.get("spotify")

    return PorterConfig(
        database_path=_resolve_path(base_dir, payload["database_path"]),
        report_output_dir=_resolve_path(base_dir, payload.get("report_output_dir", "reports")),
        mock_source_playlists_path=_resolve_path(
            base_dir,
            mock_payload["source_playlists_path"],
        ),
        mock_destination_catalog_path=_resolve_path(
            base_dir,
            mock_payload["destination_catalog_path"],
        ),
        mock_writes_path=(
            _resolve_path(base_dir, mock_payload["writes_path"])
            if mock_payload.get("writes_path")
            else None
        ),
        spotify=(
            _load_spotify_config(base_dir, spotify_payload)
            if isinstance(spotify_payload, dict)
            else None
        ),
    )


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _load_spotify_config(base_dir: Path, payload: dict[str, Any]) -> SpotifyConfig:
    scopes = _parse_scopes(payload.get("scopes"))
    cache_path_value = _expand_env(payload.get("cache_path"))
    return SpotifyConfig(
        client_id=_optional_text(_expand_env(payload.get("client_id"))),
        client_secret=_optional_text(_expand_env(payload.get("client_secret"))),
        redirect_uri=_optional_text(_expand_env(payload.get("redirect_uri"))),
        scopes=scopes or DEFAULT_SPOTIFY_SCOPES,
        cache_path=(
            _resolve_path(base_dir, cache_path_value)
            if cache_path_value is not None
            else _default_spotify_cache_path()
        ),
        create_public_playlists=bool(payload.get("create_public_playlists", False)),
    )


def _expand_env(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return os.path.expandvars(value)


def _parse_scopes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(scope for scope in value.split() if scope)
    if isinstance(value, list | tuple):
        return tuple(str(scope).strip() for scope in value if str(scope).strip())
    raise ValueError("spotify scopes must be a list or space-delimited string")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("${") and text.endswith("}"):
        return None
    return text or None


def _default_spotify_cache_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "playlist-porter" / "spotify-token-cache"
    return Path.home() / ".cache" / "playlist-porter" / "spotify-token-cache"


__all__ = [
    "DEFAULT_SPOTIFY_SCOPES",
    "PorterConfig",
    "SpotifyConfig",
    "default_config_payload",
    "load_config",
    "write_default_config",
]
