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
)


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
class TransferCommandConfig:
    """Optional defaults for the direction-aware transfer command."""

    source_platform: str | None = None
    destination_platform: str | None = None
    source_playlist: str | None = None
    dry_run: bool | None = None
    restart: bool | None = None
    database_path: Path | None = None
    output_dir: Path | None = None
    run_id: str | None = None
    destination_playlist_id: str | None = None
    create_playlist: str | None = None


@dataclass(frozen=True)
class ReviewCommandConfig:
    """Optional defaults for persisted match review."""

    database_path: Path | None = None
    run_id: str | None = None
    candidate_rank: int | None = None


@dataclass(frozen=True)
class ExecuteCommandConfig:
    """Optional defaults for mock write execution."""

    database_path: Path | None = None
    run_id: str | None = None
    destination_playlist_id: str | None = None
    create_playlist: str | None = None


@dataclass(frozen=True)
class ResumeCommandConfig:
    """Optional defaults for mock write resume."""

    database_path: Path | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class ExportReportCommandConfig:
    """Optional defaults for report export."""

    database_path: Path | None = None
    run_id: str | None = None
    output_dir: Path | None = None
    output_format: str | None = None


@dataclass(frozen=True)
class CommandConfig:
    """Optional CLI command defaults loaded from local config."""

    transfer: TransferCommandConfig = field(default_factory=TransferCommandConfig)
    review: ReviewCommandConfig = field(default_factory=ReviewCommandConfig)
    execute: ExecuteCommandConfig = field(default_factory=ExecuteCommandConfig)
    resume: ResumeCommandConfig = field(default_factory=ResumeCommandConfig)
    export_report: ExportReportCommandConfig = field(default_factory=ExportReportCommandConfig)


@dataclass(frozen=True)
class PorterConfig:
    """Resolved configuration for CLI transfer workflows."""

    database_path: Path
    report_output_dir: Path
    mock_source_playlists_path: Path
    mock_destination_catalog_path: Path
    mock_writes_path: Path | None = None
    spotify: SpotifyConfig | None = None
    qqmusic: QQMusicConfig | None = None
    commands: CommandConfig = field(default_factory=CommandConfig)


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
            "transfer": {
                "source_platform": "spotify",
                "destination_platform": "mock",
                "source_playlist": "",
                "dry_run": True,
                "restart": False,
                "output_dir": "reports",
            },
            "review": {
                "database_path": "state/playlist-porter.sqlite",
                "run_id": "",
            },
            "execute": {
                "database_path": "state/playlist-porter.sqlite",
                "run_id": "",
                "destination_playlist_id": "",
                "create_playlist": "",
            },
            "resume": {
                "database_path": "state/playlist-porter.sqlite",
                "run_id": "",
            },
            "export_report": {
                "database_path": "state/playlist-porter.sqlite",
                "run_id": "",
                "output_dir": "reports",
                "format": "both",
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
    transfer_payload = payload.get("transfer", {})
    review_payload = payload.get("review", {})
    execute_payload = payload.get("execute", {})
    resume_payload = payload.get("resume", {})
    export_payload = payload.get("export_report", {})
    return CommandConfig(
        transfer=(
            _load_transfer_command_config(base_dir, transfer_payload)
            if isinstance(transfer_payload, dict)
            else TransferCommandConfig()
        ),
        review=(
            _load_review_command_config(base_dir, review_payload)
            if isinstance(review_payload, dict)
            else ReviewCommandConfig()
        ),
        execute=(
            _load_execute_command_config(base_dir, execute_payload)
            if isinstance(execute_payload, dict)
            else ExecuteCommandConfig()
        ),
        resume=(
            _load_resume_command_config(base_dir, resume_payload)
            if isinstance(resume_payload, dict)
            else ResumeCommandConfig()
        ),
        export_report=(
            _load_export_report_command_config(base_dir, export_payload)
            if isinstance(export_payload, dict)
            else ExportReportCommandConfig()
        ),
    )


def _load_transfer_command_config(
    base_dir: Path,
    payload: dict[str, Any],
) -> TransferCommandConfig:
    return TransferCommandConfig(
        source_platform=_optional_text(payload.get("source_platform")),
        destination_platform=_optional_text(payload.get("destination_platform")),
        source_playlist=_optional_text(payload.get("source_playlist")),
        dry_run=_optional_bool(payload.get("dry_run")),
        restart=_optional_bool(payload.get("restart")),
        database_path=_optional_path(base_dir, payload.get("database_path")),
        output_dir=_optional_path(base_dir, payload.get("output_dir")),
        run_id=_optional_text(payload.get("run_id")),
        destination_playlist_id=_optional_text(payload.get("destination_playlist_id")),
        create_playlist=_optional_text(payload.get("create_playlist")),
    )


def _load_review_command_config(
    base_dir: Path,
    payload: dict[str, Any],
) -> ReviewCommandConfig:
    candidate_rank = payload.get("candidate_rank")
    return ReviewCommandConfig(
        database_path=_optional_path(base_dir, payload.get("database_path")),
        run_id=_optional_text(payload.get("run_id")),
        candidate_rank=int(candidate_rank) if candidate_rank is not None else None,
    )


def _load_execute_command_config(
    base_dir: Path,
    payload: dict[str, Any],
) -> ExecuteCommandConfig:
    return ExecuteCommandConfig(
        database_path=_optional_path(base_dir, payload.get("database_path")),
        run_id=_optional_text(payload.get("run_id")),
        destination_playlist_id=_optional_text(payload.get("destination_playlist_id")),
        create_playlist=_optional_text(payload.get("create_playlist")),
    )


def _load_resume_command_config(
    base_dir: Path,
    payload: dict[str, Any],
) -> ResumeCommandConfig:
    return ResumeCommandConfig(
        database_path=_optional_path(base_dir, payload.get("database_path")),
        run_id=_optional_text(payload.get("run_id")),
    )


def _load_export_report_command_config(
    base_dir: Path,
    payload: dict[str, Any],
) -> ExportReportCommandConfig:
    return ExportReportCommandConfig(
        database_path=_optional_path(base_dir, payload.get("database_path")),
        run_id=_optional_text(payload.get("run_id")),
        output_dir=_optional_path(base_dir, payload.get("output_dir")),
        output_format=_optional_text(payload.get("format")),
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


def _optional_path(base_dir: Path, value: Any) -> Path | None:
    text = _optional_text(value)
    if text is None:
        return None
    return _resolve_path(base_dir, text)


def _default_spotify_cache_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "playlist-porter" / "spotify-token-cache"
    return Path.home() / ".cache" / "playlist-porter" / "spotify-token-cache"


__all__ = [
    "CommandConfig",
    "DEFAULT_SPOTIFY_SCOPES",
    "ExecuteCommandConfig",
    "ExportReportCommandConfig",
    "PorterConfig",
    "QQMusicConfig",
    "ResumeCommandConfig",
    "ReviewCommandConfig",
    "SpotifyConfig",
    "TransferCommandConfig",
    "default_config_payload",
    "load_config",
    "write_default_config",
]
