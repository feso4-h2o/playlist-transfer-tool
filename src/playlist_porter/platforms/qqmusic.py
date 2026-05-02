"""QQ Music platform adapter.

The adapter wraps ``qqmusic-api-python`` behind the synchronous ``BasePlatform``
contract used by the rest of the project. Live calls are intentionally thin and
all mapping logic accepts plain dictionaries so unit tests can run without QQ
Music traffic or credentials.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from playlist_porter.models import Playlist, TrackCandidate, UniversalTrack
from playlist_porter.platforms.base import BasePlatform, PlatformCapabilities
from playlist_porter.rate_limit import (
    AuthenticationFailure,
    CircuitBreakerOpen,
    QQMusicRateLimitPolicy,
    RateLimitExceeded,
    RetryBudgetExceeded,
    TemporaryServerError,
    TransientNetworkError,
    ValidationFailure,
)

QQ_PLATFORM = "qqmusic"


class QQMusicAdapterError(RuntimeError):
    """Base QQ Music adapter error."""


class QQMusicWriteUnsupported(QQMusicAdapterError):
    """Raised when a configured client does not expose write support."""


@dataclass(frozen=True)
class QQMusicConfig:
    """Local-only QQ Music session configuration.

    ``credential_payload`` is passed to ``qqmusic_api.Credential`` when using the
    default live client facade. Store this in an ignored local JSON file, not in
    tracked source.
    """

    credential_payload: Mapping[str, Any] | None = None
    credential_path: Path | None = None
    user_id: str | None = None
    page_size: int = 100
    supports_create_playlist: bool = True
    supports_add_tracks: bool = True

    @classmethod
    def from_json(cls, path: str | Path) -> QQMusicConfig:
        """Load QQ Music local session settings from a JSON file."""

        config_path = Path(path)
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        credential_path = payload.get("credential_path")
        return cls(
            credential_payload=payload.get("credential"),
            credential_path=(
                _resolve_path(config_path.parent, credential_path) if credential_path else None
            ),
            user_id=_optional_text(payload.get("user_id")),
            page_size=int(payload.get("page_size", 100)),
            supports_create_playlist=bool(payload.get("supports_create_playlist", True)),
            supports_add_tracks=bool(payload.get("supports_add_tracks", True)),
        )

    def load_credential_payload(self) -> Mapping[str, Any] | None:
        """Return credential data from inline config or the configured local file."""

        if self.credential_payload is not None:
            return self.credential_payload
        if self.credential_path is None:
            return None
        return json.loads(self.credential_path.read_text(encoding="utf-8"))


class QQMusicClientFacade:
    """Small sync facade around ``qqmusic-api-python`` client calls."""

    def __init__(
        self,
        *,
        credential_payload: Mapping[str, Any] | None = None,
        max_concurrency: int = 1,
    ) -> None:
        try:
            from qqmusic_api import Client, Credential
        except ImportError as exc:  # pragma: no cover - dependency smoke test covers install
            raise QQMusicAdapterError("qqmusic-api-python is not installed") from exc

        credential = (
            Credential.model_validate(dict(credential_payload))
            if credential_payload is not None
            else None
        )
        self._client = Client(credential=credential, max_concurrency=max_concurrency)
        self._credential = credential

    def validate_session(self) -> None:
        """Validate that the configured QQ Music credential is usable."""

        if self._credential is None:
            raise AuthenticationFailure(
                "QQ Music credentials are missing; configure a local credential file"
            )
        if self._credential.is_expired():
            raise AuthenticationFailure("QQ Music credentials are expired; refresh local session")
        self._execute(self._client.user.get_vip_info(credential=self._credential))

    def get_playlist(self, playlist_id: int, *, page_size: int) -> Any:
        """Fetch a playlist detail page."""

        request = self._client.songlist.get_detail(playlist_id, num=page_size)
        return self._execute(request)

    def search_tracks(self, query: str, *, limit: int) -> Any:
        """Search QQ Music tracks."""

        try:
            from qqmusic_api.modules.search import SearchType
        except ImportError as exc:  # pragma: no cover
            raise QQMusicAdapterError("qqmusic-api-python search module is unavailable") from exc

        request = self._client.search.search_by_type(
            query,
            search_type=SearchType.SONG,
            num=limit,
            highlight=False,
        )
        return self._execute(request)

    def create_playlist(self, name: str) -> Any:
        """Create a QQ Music playlist."""

        request = self._client.songlist.create(name, credential=self._credential)
        return self._execute(request)

    def add_songs(self, playlist_id: int, song_info: list[tuple[int, int]]) -> bool:
        """Add songs to a QQ Music playlist."""

        return bool(
            self._run_async(
                self._client.songlist.add_songs(
                    playlist_id,
                    song_info,
                    credential=self._credential,
                )
            )
        )

    def _execute(self, request: Any) -> Any:
        return self._run_async(self._client.execute(request))

    @staticmethod
    def _run_async(awaitable: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        raise QQMusicAdapterError("QQ Music adapter cannot run inside an active event loop")


class QQMusicAdapter(BasePlatform):
    """QQ Music adapter using conservative retry and explicit capability flags."""

    platform_name = QQ_PLATFORM

    def __init__(
        self,
        *,
        config: QQMusicConfig | None = None,
        client: Any | None = None,
        client_factory: Callable[[Mapping[str, Any] | None], Any] | None = None,
        rate_limit_policy: QQMusicRateLimitPolicy | None = None,
    ) -> None:
        self.config = config or QQMusicConfig()
        self.capabilities = PlatformCapabilities(
            supports_read=True,
            supports_search=True,
            supports_write=(
                self.config.supports_create_playlist and self.config.supports_add_tracks
            ),
            supports_isrc=False,
            is_official=False,
        )
        self._client = client
        self._client_factory = client_factory or (
            lambda credential_payload: QQMusicClientFacade(
                credential_payload=credential_payload,
                max_concurrency=1,
            )
        )
        self._policy = rate_limit_policy or QQMusicRateLimitPolicy()
        self.authenticated = False

    def authenticate(self) -> None:
        """Validate the local QQ Music session before transfer work."""

        try:
            self._policy.execute(
                "qqmusic session validation",
                _retryable_qqmusic_operation(self._ensure_client().validate_session),
                request_kind="read",
            )
        except Exception as exc:
            _raise_classified_qqmusic_exception(exc)
        self.authenticated = True

    def get_playlist(self, playlist_id_or_url: str) -> Playlist:
        """Fetch and map a QQ Music playlist."""

        playlist_id = _playlist_id_from_value(playlist_id_or_url)
        try:
            payload = self._policy.execute(
                "qqmusic playlist fetch",
                _retryable_qqmusic_operation(
                    lambda: self._ensure_client().get_playlist(
                        playlist_id,
                        page_size=self.config.page_size,
                    )
                ),
                request_kind="read",
            )
        except Exception as exc:
            _raise_classified_qqmusic_exception(exc)
        return playlist_from_qqmusic_payload(payload, fallback_playlist_id=str(playlist_id))

    def search_tracks(self, query: str, limit: int = 10) -> list[TrackCandidate]:
        """Search QQ Music and return ranked internal candidates."""

        try:
            payload = self._policy.execute(
                "qqmusic track search",
                _retryable_qqmusic_operation(
                    lambda: self._ensure_client().search_tracks(query, limit=limit)
                ),
                request_kind="read",
            )
        except Exception as exc:
            _raise_classified_qqmusic_exception(exc)
        tracks = search_tracks_from_qqmusic_payload(payload)
        return [
            TrackCandidate(
                track=track,
                score=max(0.0, 1.0 - ((rank - 1) * 0.05)),
                rank=rank,
                query=query,
                evidence={
                    "search_rank": rank,
                    "qqmusic_capability": "search_by_type",
                },
            )
            for rank, track in enumerate(tracks[:limit], start=1)
        ]

    def create_playlist(self, name: str, description: str | None = None) -> str:
        """Create a QQ Music playlist when supported by the configured client."""

        del description
        if not self.config.supports_create_playlist:
            raise QQMusicWriteUnsupported("QQ Music playlist creation is disabled by config")
        try:
            payload = self._policy.execute(
                "qqmusic playlist create",
                _retryable_qqmusic_operation(lambda: self._ensure_client().create_playlist(name)),
                request_kind="write",
            )
        except Exception as exc:
            _raise_classified_qqmusic_exception(exc)
        playlist_id = _first_value(payload, "dirid", "id")
        if playlist_id is None:
            raise ValidationFailure("QQ Music create playlist response did not include an id")
        return str(playlist_id)

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Add QQ Music tracks to a playlist.

        Track IDs may be plain song IDs or ``song_id:song_type`` strings. The
        song type defaults to ``0`` when omitted because QQ Music's write API
        expects ``(song_id, song_type)`` pairs.
        """

        if not self.config.supports_add_tracks:
            raise QQMusicWriteUnsupported("QQ Music add-tracks support is disabled by config")
        song_info = [_song_info_from_track_id(track_id) for track_id in track_ids]
        try:
            added = self._policy.execute(
                "qqmusic playlist add tracks",
                _retryable_qqmusic_operation(
                    lambda: self._ensure_client().add_songs(int(playlist_id), song_info)
                ),
                request_kind="write",
            )
        except Exception as exc:
            _raise_classified_qqmusic_exception(exc)
        if not added:
            raise ValidationFailure("QQ Music rejected one or more playlist additions")

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory(self.config.load_credential_payload())
        return self._client


def playlist_from_qqmusic_payload(
    payload: Any,
    *,
    fallback_playlist_id: str | None = None,
) -> Playlist:
    """Map a QQ Music playlist response into an internal playlist."""

    info = _first_value(payload, "info", "dirinfo", "songlist_info") or {}
    playlist_id = _first_value(info, "id", "tid", "dirid") or fallback_playlist_id
    title = _first_value(info, "title", "name", "dissname") or "QQ Music Playlist"
    description = _first_value(info, "desc", "description")
    tracks = [
        track_from_qqmusic_payload(song, source_playlist_position=index)
        for index, song in enumerate(_song_items_from_playlist_payload(payload), start=1)
    ]
    return Playlist(
        name=str(title),
        platform=QQ_PLATFORM,
        platform_playlist_id=str(playlist_id) if playlist_id is not None else None,
        description=_optional_text(description),
        tracks=tracks,
    )


def search_tracks_from_qqmusic_payload(payload: Any) -> list[UniversalTrack]:
    """Map a QQ Music search response into internal tracks."""

    songs = _first_value(payload, "song", "songs", "list") or []
    return [track_from_qqmusic_payload(song) for song in songs]


def track_from_qqmusic_payload(
    payload: Any,
    *,
    source_playlist_position: int | None = None,
) -> UniversalTrack:
    """Map one QQ Music song payload into ``UniversalTrack``."""

    song_id = _first_value(payload, "mid", "songmid", "id", "songid")
    numeric_id = _optional_int(_first_value(payload, "id", "songid"))
    song_type = _optional_int(_first_value(payload, "type", "songtype")) or 0
    platform_track_id = _platform_track_id(song_id, numeric_id, song_type)
    title = _first_value(payload, "title", "name", "songname")
    artists = _artist_names(_first_value(payload, "singer", "artists", "artist"))
    album_payload = _first_value(payload, "album")
    album = _first_value(album_payload, "title", "name") if album_payload is not None else None
    release_value = _first_value(payload, "time_public", "public_time", "release_date")
    release_date = _parse_release_date(release_value)

    if title is None:
        raise ValidationFailure("QQ Music song payload is missing a title")
    if not artists:
        raise ValidationFailure("QQ Music song payload is missing artists")

    return UniversalTrack(
        internal_id=_stable_internal_id(platform_track_id, title, artists),
        title=str(title),
        artists=artists,
        platform=QQ_PLATFORM,
        platform_track_id=platform_track_id,
        album=_optional_text(album),
        duration_seconds=_optional_int(
            _first_value(payload, "interval", "duration", "duration_seconds")
        ),
        release_date=release_date,
        release_year=release_date.year if release_date is not None else None,
        explicit=None,
        source_playlist_position=source_playlist_position,
    )


def _song_items_from_playlist_payload(payload: Any) -> Sequence[Any]:
    songs = _first_value(payload, "songs", "songlist", "song", "list")
    if songs is None:
        return []
    if not isinstance(songs, Sequence) or isinstance(songs, str | bytes):
        raise ValidationFailure("QQ Music playlist response did not include a song list")
    return songs


def _artist_names(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [artist.strip() for artist in payload.replace("|", ";").split(";") if artist.strip()]
    if isinstance(payload, Sequence):
        names = []
        for artist in payload:
            name = _first_value(artist, "name", "title")
            if name is not None and str(name).strip():
                names.append(str(name).strip())
        return names
    name = _first_value(payload, "name", "title")
    return [str(name).strip()] if name is not None and str(name).strip() else []


def _platform_track_id(song_id: Any, numeric_id: int | None, song_type: int) -> str | None:
    if numeric_id is not None:
        return f"{numeric_id}:{song_type}"
    if song_id is None:
        return None
    return str(song_id)


def _song_info_from_track_id(track_id: str) -> tuple[int, int]:
    song_id, _, song_type = track_id.partition(":")
    try:
        return int(song_id), int(song_type or 0)
    except ValueError as exc:
        raise ValidationFailure(f"QQ Music write requires numeric song ids: {track_id}") from exc


def _playlist_id_from_value(value: str) -> int:
    text = value.strip().rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if "id=" in text:
        text = text.split("id=", 1)[1].split("&", 1)[0]
    try:
        return int(text)
    except ValueError as exc:
        raise ValidationFailure(f"QQ Music playlist id must be numeric: {value}") from exc


def _classify_qqmusic_exception(exc: Exception) -> Exception:
    if isinstance(
        exc,
        AuthenticationFailure
        | CircuitBreakerOpen
        | RateLimitExceeded
        | RetryBudgetExceeded
        | TemporaryServerError
        | TransientNetworkError
        | ValidationFailure
        | QQMusicAdapterError,
    ):
        return exc

    name = exc.__class__.__name__.casefold()
    message = str(exc) or exc.__class__.__name__
    if name in {"loginerror", "loginexpirederror", "notloginerror", "signinvaliderror"}:
        return AuthenticationFailure(f"QQ Music session is invalid: {message}")
    if name in {"ratelimitederror"}:
        return RateLimitExceeded(f"QQ Music rate limit or throttling signal: {message}")
    if name in {"networkerror", "httperror"}:
        return TransientNetworkError(f"QQ Music network failure: {message}")
    if name in {"apierror"}:
        code = getattr(exc, "code", None)
        if isinstance(code, int) and 500 <= code <= 599:
            return TemporaryServerError(f"QQ Music temporary server failure: {message}")
        return ValidationFailure(f"QQ Music API rejected the request: {message}")
    return QQMusicAdapterError(f"QQ Music adapter operation failed: {message}")


def _retryable_qqmusic_operation[T](operation: Callable[[], T]) -> Callable[[], T]:
    def wrapped() -> T:
        try:
            return operation()
        except Exception as exc:
            _raise_classified_qqmusic_exception(exc)

    return wrapped


def _raise_classified_qqmusic_exception(exc: Exception) -> None:
    classified = _classify_qqmusic_exception(exc)
    if classified is exc:
        raise exc
    raise classified from exc


def _first_value(payload: Any, *keys: str) -> Any:
    if payload is None:
        return None
    for key in keys:
        if isinstance(payload, Mapping) and key in payload:
            return payload[key]
        if hasattr(payload, key):
            return getattr(payload, key)
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_release_date(value: Any) -> date | None:
    text = _optional_text(value)
    if text is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parts = [int(part) for part in text.split("-")]
            if fmt == "%Y-%m-%d" and len(parts) == 3:
                return date(parts[0], parts[1], parts[2])
            if fmt == "%Y-%m" and len(parts) == 2:
                return date(parts[0], parts[1], 1)
            if fmt == "%Y" and len(parts) == 1:
                return date(parts[0], 1, 1)
        except ValueError:
            continue
    return None


def _stable_internal_id(
    platform_track_id: str | None,
    title: Any,
    artists: Sequence[str],
) -> UUID:
    identity = platform_track_id or json.dumps(
        {"title": title, "artists": list(artists)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return uuid5(NAMESPACE_URL, f"qqmusic-track:{identity}")


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


__all__ = [
    "QQMusicAdapter",
    "QQMusicAdapterError",
    "QQMusicClientFacade",
    "QQMusicConfig",
    "QQMusicWriteUnsupported",
    "playlist_from_qqmusic_payload",
    "search_tracks_from_qqmusic_payload",
    "track_from_qqmusic_payload",
]
