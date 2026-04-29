"""Core validated data models for the playlist transfer pipeline."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.normalization import track_fingerprint

EvidenceValue = str | int | float | bool | None


class UniversalTrack(BaseModel):
    """Platform-neutral track metadata.

    ``track_fingerprint`` is a deterministic cache/grouping key derived from
    normalized title and primary artist. It is not an identity proof and should
    not be used to auto-confirm a match.
    """

    model_config = ConfigDict(validate_assignment=True)

    internal_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    artists: list[str] = Field(min_length=1)
    platform: str | None = None
    platform_track_id: str | None = None
    album: str | None = None
    isrc: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    release_date: date | None = None
    release_year: int | None = Field(default=None, ge=1800, le=2200)
    explicit: bool | None = None
    source_playlist_position: int | None = Field(default=None, ge=0)

    @field_validator("title", "platform", "platform_track_id", "album", "isrc", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("title")
    @classmethod
    def _title_is_required(cls, value: str | None) -> str:
        if not value:
            raise ValueError("title must not be empty")
        return value

    @field_validator("artists", mode="before")
    @classmethod
    def _normalize_artists(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("artists must be a list of strings")

        artists = [artist.strip() for artist in value if isinstance(artist, str) and artist.strip()]
        if not artists:
            raise ValueError("artists must contain at least one non-empty artist")
        return artists

    @computed_field
    @property
    def primary_artist(self) -> str:
        return self.artists[0]

    @computed_field
    @property
    def track_fingerprint(self) -> str:
        return track_fingerprint(self.title, self.primary_artist)


class Playlist(BaseModel):
    """Platform-neutral playlist with ordered source tracks."""

    internal_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    tracks: list[UniversalTrack] = Field(default_factory=list)
    platform: str | None = None
    platform_playlist_id: str | None = None
    description: str | None = None
    owner_id: str | None = None
    source_url: str | None = None

    @field_validator(
        "name",
        "platform",
        "platform_playlist_id",
        "description",
        "owner_id",
        "source_url",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("name")
    @classmethod
    def _name_is_required(cls, value: str | None) -> str:
        if not value:
            raise ValueError("name must not be empty")
        return value


class TrackCandidate(BaseModel):
    """A possible destination-platform track for one source track."""

    track: UniversalTrack
    score: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)
    query: str | None = None
    evidence: dict[str, EvidenceValue] = Field(default_factory=dict)
    unavailable_reason: UnavailableReason | None = None


class MatchDecision(BaseModel):
    """Recorded matching decision for a source track."""

    source_track: UniversalTrack
    status: MatchStatus
    candidates: list[TrackCandidate] = Field(default_factory=list)
    selected_candidate: TrackCandidate | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: dict[str, EvidenceValue] = Field(default_factory=dict)
    reason_codes: list[UnavailableReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def _selected_candidate_must_be_listed(self) -> MatchDecision:
        if self.selected_candidate is None:
            return self

        selected_id = self.selected_candidate.track.internal_id
        candidate_ids = {candidate.track.internal_id for candidate in self.candidates}
        if selected_id not in candidate_ids:
            self.candidates.insert(0, self.selected_candidate)
        return self


class TransferRun(BaseModel):
    """Top-level state for one source-to-destination transfer attempt."""

    internal_id: UUID = Field(default_factory=uuid4)
    source_platform: str = Field(min_length=1)
    destination_platform: str = Field(min_length=1)
    source_playlist: Playlist | None = None
    destination_playlist_id: str | None = None
    dry_run: bool = True
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    decisions: list[MatchDecision] = Field(default_factory=list)
    metadata: dict[str, EvidenceValue] = Field(default_factory=dict)

    @field_validator(
        "source_platform",
        "destination_platform",
        "destination_playlist_id",
        mode="before",
    )
    @classmethod
    def _strip_run_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


__all__ = [
    "MatchDecision",
    "MatchStatus",
    "Playlist",
    "TrackCandidate",
    "TransferRun",
    "UnavailableReason",
    "UniversalTrack",
]
