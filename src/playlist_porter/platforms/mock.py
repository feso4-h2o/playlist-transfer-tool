"""Local fixture-backed platform adapter used by matching tests and dry runs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from playlist_porter.matching.status import UnavailableReason
from playlist_porter.models import Playlist, TrackCandidate, UniversalTrack
from playlist_porter.normalization import (
    normalize_text,
    normalize_text_forms,
    normalize_title_forms,
)
from playlist_porter.platforms.base import BasePlatform, PlatformCapabilities


@dataclass(frozen=True)
class _CatalogEntry:
    track: UniversalTrack
    unavailable_reason: UnavailableReason | None = None
    popularity: int | None = None


class MockAdapter(BasePlatform):
    """Deterministic local adapter backed by in-memory or fixture metadata."""

    platform_name = "mock"
    capabilities = PlatformCapabilities(
        supports_read=True,
        supports_search=True,
        supports_write=True,
        supports_isrc=True,
        is_official=False,
    )

    def __init__(
        self,
        *,
        playlists: dict[str, Playlist] | None = None,
        catalog: list[UniversalTrack] | None = None,
        catalog_entries: list[dict[str, Any]] | None = None,
        writes_path: str | Path | None = None,
        min_query_score: float = 0.70,
    ) -> None:
        self._playlists = playlists or {}
        self._catalog = self._build_catalog(catalog or [], catalog_entries or [])
        self._writes_path = Path(writes_path) if writes_path is not None else None
        self._writes: dict[str, dict[str, Any]] = {}
        self._min_query_score = min_query_score
        self.authenticated = False

    @classmethod
    def from_json(
        cls,
        *,
        playlists_path: str | Path,
        catalog_path: str | Path,
        writes_path: str | Path | None = None,
    ) -> MockAdapter:
        """Load playlists and destination catalog from JSON fixture files."""

        playlists_payload = json.loads(Path(playlists_path).read_text(encoding="utf-8"))
        catalog_payload = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        return cls(
            playlists=_load_json_playlists(playlists_payload),
            catalog_entries=_extract_records(catalog_payload, "catalog"),
            writes_path=writes_path,
        )

    @classmethod
    def from_csv(
        cls,
        *,
        playlist_path: str | Path,
        catalog_path: str | Path,
        playlist_id: str = "fixture-playlist",
        playlist_name: str = "Fixture Playlist",
        writes_path: str | Path | None = None,
    ) -> MockAdapter:
        """Load one source playlist and one destination catalog from CSV fixtures."""

        playlist_records = _read_csv_records(playlist_path)
        catalog_records = _read_csv_records(catalog_path)
        playlist = Playlist(
            name=playlist_name,
            platform="mock",
            platform_playlist_id=playlist_id,
            tracks=[_track_from_record(record, platform="mock") for record in playlist_records],
        )
        return cls(
            playlists={playlist_id: playlist},
            catalog_entries=catalog_records,
            writes_path=writes_path,
        )

    def authenticate(self) -> None:
        self.authenticated = True

    def get_playlist(self, playlist_id_or_url: str) -> Playlist:
        try:
            return self._playlists[playlist_id_or_url]
        except KeyError as exc:
            raise ValueError(f"mock playlist not found: {playlist_id_or_url}") from exc

    def search_tracks(self, query: str, limit: int = 10) -> list[TrackCandidate]:
        normalized_query_forms = normalize_text_forms(query)
        if not normalized_query_forms:
            return []

        ranked: list[tuple[float, _CatalogEntry]] = []

        for entry in self._catalog:
            haystack_forms = _searchable_forms(entry.track)
            score = max(
                fuzz.token_set_ratio(query_form, haystack_form) / 100
                for query_form in normalized_query_forms
                for haystack_form in haystack_forms
            )
            if score >= self._min_query_score:
                ranked.append((score, entry))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].popularity if item[1].popularity is not None else 0,
            ),
            reverse=True,
        )

        candidates: list[TrackCandidate] = []
        for rank, (score, entry) in enumerate(ranked[:limit], start=1):
            candidates.append(
                TrackCandidate(
                    track=entry.track,
                    score=score,
                    rank=rank,
                    query=query,
                    evidence={
                        "search_query_score": round(score, 4),
                        "popularity": entry.popularity,
                    },
                    unavailable_reason=entry.unavailable_reason,
                )
            )
        return candidates

    def create_playlist(self, name: str, description: str | None = None) -> str:
        playlist_id = f"mock-created-{len(self._writes) + 1}"
        self._writes[playlist_id] = {
            "name": name,
            "description": description,
            "track_ids": [],
        }
        self._flush_writes()
        return playlist_id

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        if playlist_id not in self._writes:
            self._writes[playlist_id] = {
                "name": None,
                "description": None,
                "track_ids": [],
            }
        self._writes[playlist_id]["track_ids"].extend(track_ids)
        self._flush_writes()

    @property
    def writes(self) -> dict[str, dict[str, Any]]:
        return self._writes

    def _flush_writes(self) -> None:
        if self._writes_path is None:
            return
        self._writes_path.parent.mkdir(parents=True, exist_ok=True)
        self._writes_path.write_text(
            json.dumps(self._writes, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _build_catalog(
        tracks: list[UniversalTrack],
        catalog_entries: list[dict[str, Any]],
    ) -> list[_CatalogEntry]:
        entries = [_CatalogEntry(track=track) for track in tracks]
        entries.extend(_catalog_entry_from_record(record) for record in catalog_entries)
        return entries


def _extract_records(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    raise ValueError(f"expected a list or object with a '{key}' list")


def _load_json_playlists(payload: Any) -> dict[str, Playlist]:
    records = _extract_records(payload, "playlists")
    playlists: dict[str, Playlist] = {}
    for record in records:
        playlist_id = str(record["id"])
        playlists[playlist_id] = Playlist(
            name=record["name"],
            platform=record.get("platform", "mock"),
            platform_playlist_id=playlist_id,
            description=record.get("description"),
            owner_id=record.get("owner_id"),
            source_url=record.get("source_url"),
            tracks=[
                _track_from_record(track, platform=record.get("platform", "mock"))
                for track in record.get("tracks", [])
            ],
        )
    return playlists


def _read_csv_records(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _catalog_entry_from_record(record: dict[str, Any]) -> _CatalogEntry:
    reason_value = _blank_to_none(record.get("unavailable_reason"))
    popularity_value = _blank_to_none(record.get("popularity"))
    return _CatalogEntry(
        track=_track_from_record(record, platform=record.get("platform", "mock")),
        unavailable_reason=UnavailableReason(reason_value) if reason_value else None,
        popularity=int(popularity_value) if popularity_value is not None else None,
    )


def _track_from_record(record: dict[str, Any], *, platform: str) -> UniversalTrack:
    return UniversalTrack(
        title=record["title"],
        artists=_parse_artists(record["artists"]),
        platform=record.get("platform", platform),
        platform_track_id=_first_optional_text(
            record.get("platform_track_id"),
            record.get("id"),
        ),
        album=_optional_text(record.get("album")),
        isrc=_optional_text(record.get("isrc")),
        duration_seconds=_optional_int(record.get("duration_seconds")),
        release_date=_optional_text(record.get("release_date")),
        release_year=_optional_int(record.get("release_year")),
        explicit=_optional_bool(record.get("explicit")),
        source_playlist_position=_optional_int(record.get("source_playlist_position")),
    )


def _searchable_forms(track: UniversalTrack) -> tuple[str, ...]:
    artists = " ".join(track.artists)
    forms: list[str] = []
    for title in normalize_title_forms(track.title):
        artist_forms = normalize_text_forms(track.primary_artist) + normalize_text_forms(artists)
        for artist_form in artist_forms:
            forms.append(normalize_text(f"{title.core} {artist_form}"))
            forms.append(normalize_text(f"{title.full} {artist_form}"))
    return tuple(dict.fromkeys(form for form in forms if form))


def _parse_artists(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(artist) for artist in value]
    if isinstance(value, str):
        return [artist.strip() for artist in value.replace("|", ";").split(";")]
    raise ValueError("artists must be a list or separator-delimited string")


def _blank_to_none(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _optional_text(value: Any) -> str | None:
    value = _blank_to_none(value)
    return str(value) if value is not None else None


def _first_optional_text(*values: Any) -> str | None:
    for value in values:
        text = _optional_text(value)
        if text is not None:
            return text
    return None


def _optional_int(value: Any) -> int | None:
    value = _blank_to_none(value)
    return int(value) if value is not None else None


def _optional_bool(value: Any) -> bool | None:
    value = _blank_to_none(value)
    if value is None or isinstance(value, bool):
        return value
    return str(value).casefold() in {"1", "true", "yes", "y"}


__all__ = ["MockAdapter"]
