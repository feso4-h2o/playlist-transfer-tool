"""Local JSON configuration for CLI workflows."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playlist_porter.platforms.qqmusic import QQMusicConfig

DEFAULT_SPOTIFY_SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-read",
    "user-library-modify",
)

DESTINATION_TARGET_TYPES = {"playlist", "liked_songs"}


@dataclass(frozen=True)
class SpotifyConfig:
    """Local Spotify authentication settings."""

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
class MatchCommandConfig:
    """Optional defaults for the matching command."""

    source_playlist: str | None = None
    restart: bool | None = None


@dataclass(frozen=True)
class WriteCommandConfig:
    """Optional defaults for reviewed transfer writes."""

    destination_playlist_id: str | None = None
    create_playlist: str | None = None
    destination_target_type: str = "playlist"


@dataclass(frozen=True)
class ReviewCommandConfig:
    """Optional defaults for manual review."""

    pending_only: bool | None = None


@dataclass(frozen=True)
class CommandConfig:
    """Optional CLI command defaults loaded from local config."""

    match: MatchCommandConfig = field(default_factory=MatchCommandConfig)
    review: ReviewCommandConfig = field(default_factory=ReviewCommandConfig)
    write: WriteCommandConfig = field(default_factory=WriteCommandConfig)


@dataclass(frozen=True)
class PorterConfig:
    """Resolved configuration for CLI transfer workflows."""

    database_path: Path
    report_output_dir: Path
    mock_source_playlists_path: Path
    mock_destination_catalog_path: Path
    report_format: str = "json"
    run_id: str | None = None
    source_platform: str | None = None
    destination_platform: str | None = None
    mock_writes_path: Path | None = None
    spotify: SpotifyConfig | None = None
    qqmusic: QQMusicConfig | None = None
    commands: CommandConfig = field(default_factory=CommandConfig)


def default_config_payload() -> dict[str, Any]:
    """Return a credential-free starter configuration."""

    return {
        "database_path": "state/playlist-porter.sqlite",
        "destination_platform": "mock",
        "report_output_dir": "reports",
        "report_format": "json",
        "run_id": "",
        "source_platform": "mock",
        "mock": {
            "source_playlists_path": "fixtures/mock-playlists.json",
            "destination_catalog_path": "fixtures/mock-catalog.json",
            "writes_path": "state/mock-writes.json",
        },
        "spotify": {
            "cache_path": str(_default_spotify_cache_path()),
            "create_public_playlists": False,
        },
        "qqmusic": {
            "page_size": 100,
            "supports_create_playlist": True,
            "supports_add_tracks": True,
            "allow_anonymous_read": True,
        },
        "commands": {
            "match": {
                "source_playlist": "",
                "restart": False,
            },
            "review": {
                "pending_only": False,
            },
            "write": {
                "destination_playlist_id": "",
                "create_playlist": "",
                "destination_target_type": "playlist",
            },
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


def update_config_run_id(path: str | Path, run_id: str) -> Path:
    """Persist the latest run id in a local JSON config."""

    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["run_id"] = run_id
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
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
    qqmusic_payload = payload.get("qqmusic")
    commands_payload = payload.get("commands")

    return PorterConfig(
        database_path=_resolve_path(base_dir, payload["database_path"]),
        report_output_dir=_resolve_path(base_dir, payload.get("report_output_dir", "reports")),
        report_format=_report_format(payload.get("report_format")),
        run_id=_optional_text(payload.get("run_id")),
        source_platform=_optional_text(payload.get("source_platform")),
        destination_platform=_optional_text(payload.get("destination_platform")),
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
        qqmusic=(
            _load_qqmusic_config(base_dir, qqmusic_payload)
            if isinstance(qqmusic_payload, dict)
            else None
        ),
        commands=(
            _load_command_config(base_dir, commands_payload)
            if isinstance(commands_payload, dict)
            else CommandConfig()
        ),
    )


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _load_spotify_config(base_dir: Path, payload: dict[str, Any]) -> SpotifyConfig:
    env_config = SpotifyConfig.from_env()
    cache_path_value = _optional_text(_expand_env(payload.get("cache_path")))
    return SpotifyConfig(
        client_id=env_config.client_id,
        client_secret=env_config.client_secret,
        redirect_uri=env_config.redirect_uri,
        scopes=env_config.scopes,
        cache_path=(
            _resolve_path(base_dir, cache_path_value)
            if cache_path_value is not None
            else _default_spotify_cache_path()
        ),
        create_public_playlists=bool(payload.get("create_public_playlists", False)),
    )


def _load_qqmusic_config(base_dir: Path, payload: dict[str, Any]) -> QQMusicConfig:
    del base_dir
    env_config = QQMusicConfig.from_env()
    return QQMusicConfig(
        credential_path=env_config.credential_path,
        user_id=env_config.user_id,
        page_size=int(payload.get("page_size", 100)),
        supports_create_playlist=bool(payload.get("supports_create_playlist", True)),
        supports_add_tracks=bool(payload.get("supports_add_tracks", True)),
        allow_anonymous_read=bool(payload.get("allow_anonymous_read", True)),
    )


def _load_command_config(base_dir: Path, payload: dict[str, Any]) -> CommandConfig:
    del base_dir
    match_payload = payload.get("match", {})
    review_payload = payload.get("review", {})
    write_payload = payload.get("write", {})
    return CommandConfig(
        match=(
            _load_match_command_config(match_payload)
            if isinstance(match_payload, dict)
            else MatchCommandConfig()
        ),
        review=(
            _load_review_command_config(review_payload)
            if isinstance(review_payload, dict)
            else ReviewCommandConfig()
        ),
        write=(
            _load_write_command_config(write_payload)
            if isinstance(write_payload, dict)
            else WriteCommandConfig()
        ),
    )


def _load_match_command_config(payload: dict[str, Any]) -> MatchCommandConfig:
    return MatchCommandConfig(
        source_playlist=_optional_text(payload.get("source_playlist")),
        restart=_optional_bool(payload.get("restart")),
    )


def _load_review_command_config(payload: dict[str, Any]) -> ReviewCommandConfig:
    return ReviewCommandConfig(
        pending_only=_optional_bool(payload.get("pending_only")),
    )


def _load_write_command_config(payload: dict[str, Any]) -> WriteCommandConfig:
    return WriteCommandConfig(
        destination_playlist_id=_optional_text(payload.get("destination_playlist_id")),
        create_playlist=_optional_text(payload.get("create_playlist")),
        destination_target_type=_destination_target_type(
            payload.get("destination_target_type")
        ),
    )


def _expand_env(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return os.path.expandvars(value)


def _parse_scopes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            return ()
        return tuple(scope for scope in value.split() if scope)
    if isinstance(value, list | tuple):
        scopes: list[str] = []
        for scope in value:
            expanded = _expand_env(scope)
            if isinstance(expanded, str) and expanded.startswith("${") and expanded.endswith("}"):
                continue
            scopes.extend(str(expanded).split())
        return tuple(scope.strip() for scope in scopes if scope.strip())
    raise ValueError("spotify scopes must be a list or space-delimited string")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("${") and text.endswith("}"):
        return None
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if not text:
            return None
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _report_format(value: Any) -> str:
    report_format = _optional_text(value) or "json"
    if report_format not in {"csv", "json", "both"}:
        raise ValueError("report_format must be one of csv, json, both")
    return report_format


def _destination_target_type(value: Any) -> str:
    target_type = _optional_text(value) or "playlist"
    if target_type not in DESTINATION_TARGET_TYPES:
        raise ValueError("destination_target_type must be one of playlist, liked_songs")
    return target_type


def _default_spotify_cache_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "playlist-porter" / "spotify-token-cache"
    return Path.home() / ".cache" / "playlist-porter" / "spotify-token-cache"


__all__ = [
    "CommandConfig",
    "DESTINATION_TARGET_TYPES",
    "DEFAULT_SPOTIFY_SCOPES",
    "MatchCommandConfig",
    "PorterConfig",
    "QQMusicConfig",
    "ReviewCommandConfig",
    "SpotifyConfig",
    "WriteCommandConfig",
    "default_config_payload",
    "load_config",
    "update_config_run_id",
    "write_default_config",
]
