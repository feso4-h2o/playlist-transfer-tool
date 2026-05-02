"""Spotify Web API adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import requests
from rapidfuzz import fuzz
from spotipy import Spotify
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from playlist_porter.config import SpotifyConfig
from playlist_porter.matching.status import UnavailableReason
from playlist_porter.models import Playlist, TrackCandidate, UniversalTrack
from playlist_porter.normalization import normalize_text, normalize_text_forms
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.platforms.base import BasePlatform, PlatformCapabilities
from playlist_porter.rate_limit import (
    AuthenticationFailure,
    RateLimitExceeded,
    RollingWindowLimiter,
    SpotifyRateLimitPolicy,
    TemporaryServerError,
    TransientNetworkError,
    ValidationFailure,
)

SPOTIFY_BATCH_LIMIT = 100


class SpotifyAdapter(BasePlatform):
    """Official Spotify adapter backed by Spotipy."""

    platform_name = "spotify"
    capabilities = PlatformCapabilities(
        supports_read=True,
        supports_search=True,
        supports_write=True,
        supports_isrc=True,
        is_official=True,
    )

    def __init__(
        self,
        config: SpotifyConfig | None = None,
        *,
        client: Any | None = None,
        rate_limit_policy: SpotifyRateLimitPolicy | None = None,
    ) -> None:
        self.config = config or SpotifyConfig.from_env()
        self._client = client
        self._authenticated = client is not None
        self.rate_limit_policy = rate_limit_policy or SpotifyRateLimitPolicy(
            limiter=RollingWindowLimiter(max_requests=90),
        )

    def authenticate(self) -> None:
        """Create a Spotipy client using OAuth credentials."""

        if self._client is not None:
            self._authenticated = True
            return

        missing = self.config.missing_credentials()
        if missing:
            missing_text = ", ".join(missing)
            raise AuthenticationFailure(
                f"missing Spotify OAuth configuration: {missing_text}"
            )

        cache_path = self.config.resolved_cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        auth_manager = SpotifyOAuth(
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            redirect_uri=self.config.redirect_uri,
            scope=self.config.scope_string,
            cache_path=str(cache_path),
            open_browser=False,
        )
        self._client = Spotify(auth_manager=auth_manager)
        self._authenticated = True

    def get_playlist(self, playlist_id_or_url: str) -> Playlist:
        """Fetch a Spotify playlist and convert all track pages to internal models."""

        client = self._client_or_raise()
        playlist_id = _playlist_id_from_input(playlist_id_or_url)
        playlist_payload = self._call(
            "spotify playlist metadata",
            lambda: client.playlist(
                playlist_id,
                fields="id,name,description,owner(id),external_urls",
            ),
        )
        tracks = [
            _track_from_spotify_payload(item["track"], position=position)
            for position, item in enumerate(self._iter_playlist_items(playlist_id))
            if _is_playable_track_item(item)
        ]

        return Playlist(
            name=playlist_payload["name"],
            platform="spotify",
            platform_playlist_id=playlist_payload["id"],
            description=playlist_payload.get("description"),
            owner_id=(playlist_payload.get("owner") or {}).get("id"),
            source_url=(playlist_payload.get("external_urls") or {}).get("spotify"),
            tracks=tracks,
        )

    def search_tracks(self, query: str, limit: int = 10) -> list[TrackCandidate]:
        """Search Spotify tracks and return ranked candidates."""

        client = self._client_or_raise()
        search_limit = max(1, min(limit, 50))
        payload = self._call(
            "spotify track search",
            lambda: client.search(q=query, type="track", limit=search_limit),
        )
        items = ((payload.get("tracks") or {}).get("items") or [])[:search_limit]
        candidates: list[TrackCandidate] = []
        for rank, item in enumerate(items, start=1):
            track = _track_from_spotify_payload(item)
            restriction_reason = _spotify_restriction_reason(item)
            candidates.append(
                TrackCandidate(
                    track=track,
                    score=_search_score(query, track),
                    rank=rank,
                    query=query,
                    evidence={
                        "spotify_popularity": item.get("popularity"),
                        "spotify_search_rank": rank,
                        "spotify_uri": item.get("uri"),
                        "spotify_is_playable": item.get("is_playable"),
                        "spotify_restriction_reason": restriction_reason,
                    },
                    unavailable_reason=(
                        UnavailableReason.REGION_UNAVAILABLE
                        if restriction_reason is not None
                        else None
                    ),
                )
            )
        return candidates

    def create_playlist(self, name: str, description: str | None = None) -> str:
        """Create a Spotify playlist for the authenticated user."""

        client = self._client_or_raise()
        user_payload = self._call("spotify current user", client.current_user)
        playlist_payload = self._call(
            "spotify create playlist",
            lambda: client.user_playlist_create(
                user_payload["id"],
                name,
                public=self.config.create_public_playlists,
                description=description or "",
            ),
        )
        return playlist_payload["id"]

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Append Spotify tracks in API-sized batches."""

        client = self._client_or_raise()
        for batch in _batched(track_ids, SPOTIFY_BATCH_LIMIT):
            uris = [_spotify_track_uri(track_id) for track_id in batch]
            self._call(
                "spotify add playlist items",
                lambda uris=uris: client.playlist_add_items(playlist_id, uris),
            )

    def add_tracks_with_progress(
        self,
        playlist_id: str,
        source_track_ids: list[str],
        track_ids: list[str],
        *,
        repository: TransferRepository,
        transfer_run_id: str,
    ) -> int:
        """Append pending tracks and record write progress after each successful batch."""

        if len(source_track_ids) != len(track_ids):
            raise ValueError("source_track_ids must match track_ids length")

        pending_pairs = [
            (source_track_id, track_id)
            for source_track_id, track_id in zip(source_track_ids, track_ids, strict=True)
            if repository.should_write_track(transfer_run_id, source_track_id, track_id)
        ]
        for batch in _batched(pending_pairs, SPOTIFY_BATCH_LIMIT):
            self.add_tracks(playlist_id, [track_id for _, track_id in batch])
            for source_track_id, track_id in batch:
                repository.record_write_success(transfer_run_id, source_track_id, track_id)
        return len(pending_pairs)

    def _iter_playlist_items(self, playlist_id: str) -> Iterable[dict[str, Any]]:
        client = self._client_or_raise()
        offset = 0
        while True:
            page = self._call(
                "spotify playlist items",
                lambda offset=offset: client.playlist_items(
                    playlist_id,
                    limit=SPOTIFY_BATCH_LIMIT,
                    offset=offset,
                    additional_types=("track",),
                ),
            )
            items = page.get("items") or []
            yield from items
            offset += len(items)
            if not page.get("next") or not items:
                break

    def _client_or_raise(self) -> Any:
        if not self._authenticated or self._client is None:
            self.authenticate()
        if self._client is None:
            raise AuthenticationFailure("Spotify client is not authenticated")
        return self._client

    def _call(self, operation_name: str, operation: Callable[[], Any]) -> Any:
        return self.rate_limit_policy.execute(
            operation_name,
            lambda: _invoke_spotify_operation(operation),
        )


def _invoke_spotify_operation(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except SpotifyException as exc:
        raise _spotify_policy_error(exc) from exc
    except requests.RequestException as exc:
        raise TransientNetworkError(str(exc)) from exc


def _spotify_policy_error(exc: SpotifyException) -> Exception:
    status = getattr(exc, "http_status", None)
    if status in {401, 403}:
        return AuthenticationFailure(str(exc))
    if status == 429:
        return RateLimitExceeded(
            str(exc),
            retry_after_seconds=_retry_after_seconds(exc),
        )
    if isinstance(status, int) and status >= 500:
        return TemporaryServerError(str(exc))
    return ValidationFailure(str(exc))


def _spotify_restriction_reason(payload: dict[str, Any]) -> str | None:
    restrictions = payload.get("restrictions") or {}
    reason = _optional_text(restrictions.get("reason"))
    if reason is not None:
        return reason
    if payload.get("is_playable") is False:
        return "not_playable"
    return None


def _search_score(query: str, track: UniversalTrack) -> float:
    searchable = normalize_text(f"{track.title} {' '.join(track.artists)}")
    query_forms = normalize_text_forms(query)
    searchable_forms = normalize_text_forms(searchable)
    if not query_forms or not searchable_forms:
        return 0.0
    return round(
        max(
            fuzz.token_set_ratio(query_form, searchable_form) / 100
            for query_form in query_forms
            for searchable_form in searchable_forms
        ),
        4,
    )


def _retry_after_seconds(exc: SpotifyException) -> float | None:
    headers = getattr(exc, "headers", None) or {}
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None


def _playlist_id_from_input(value: str) -> str:
    stripped = value.strip()
    if "open.spotify.com/playlist/" in stripped:
        return stripped.split("open.spotify.com/playlist/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    if stripped.startswith("spotify:playlist:"):
        return stripped.rsplit(":", 1)[-1]
    return stripped


def _is_playable_track_item(item: dict[str, Any]) -> bool:
    track = item.get("track")
    return (
        isinstance(track, dict)
        and track.get("type", "track") == "track"
        and bool(track.get("id"))
    )


def _track_from_spotify_payload(
    payload: dict[str, Any],
    *,
    position: int | None = None,
) -> UniversalTrack:
    album = payload.get("album") or {}
    external_ids = payload.get("external_ids") or {}
    release_date_value = _optional_text(album.get("release_date"))
    platform_track_id = _optional_text(payload.get("id"))
    stable_id = f"spotify-track:{platform_track_id or payload.get('uri')}"
    return UniversalTrack(
        internal_id=uuid5(NAMESPACE_URL, stable_id),
        title=payload["name"],
        artists=[
            artist["name"]
            for artist in payload.get("artists", [])
            if isinstance(artist, dict) and artist.get("name")
        ],
        platform="spotify",
        platform_track_id=platform_track_id,
        album=_optional_text(album.get("name")),
        isrc=_optional_text(external_ids.get("isrc")),
        duration_seconds=_duration_seconds(payload.get("duration_ms")),
        release_date=_full_release_date(release_date_value),
        release_year=_release_year(release_date_value),
        explicit=payload.get("explicit"),
        source_playlist_position=position,
    )


def _duration_seconds(value: Any) -> int | None:
    if value is None:
        return None
    return round(int(value) / 1000)


def _full_release_date(value: str | None) -> date | None:
    if value is None or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _release_year(value: str | None) -> int | None:
    if value is None or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _spotify_track_uri(track_id: str) -> str:
    if track_id.startswith("spotify:track:"):
        return track_id
    return f"spotify:track:{track_id}"


def _batched[T](items: list[T], size: int) -> Iterable[list[T]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["SPOTIFY_BATCH_LIMIT", "SpotifyAdapter"]
