"""Base platform adapter contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from playlist_porter.models import Playlist, TrackCandidate
from playlist_porter.rate_limit import ValidationFailure


@dataclass(frozen=True)
class PlatformCapabilities:
    """Feature flags exposed by a platform adapter."""

    supports_read: bool = True
    supports_search: bool = True
    supports_write: bool = False
    supports_isrc: bool = False
    is_official: bool = False


class BasePlatform(ABC):
    """Common contract for source and destination platform adapters."""

    platform_name: str
    capabilities: PlatformCapabilities
    normalizes_destination_playlist_ids: bool = False

    @abstractmethod
    def authenticate(self) -> None:
        """Prepare the adapter for read, search, or write operations."""

    @abstractmethod
    def get_playlist(self, playlist_id_or_url: str) -> Playlist:
        """Return a platform playlist converted to internal models."""

    @abstractmethod
    def search_tracks(self, query: str, limit: int = 10) -> list[TrackCandidate]:
        """Search destination catalog and return ranked raw candidates."""

    @abstractmethod
    def create_playlist(self, name: str, description: str | None = None) -> str:
        """Create a destination playlist and return its platform ID."""

    @abstractmethod
    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Append destination-platform track IDs to a playlist."""

    def get_destination_track_ids(self, playlist_id: str) -> set[str]:
        """Return destination-platform track IDs already present in a playlist.

        Platforms that cannot read destination contents may leave this as an
        empty no-op; the write path still remains resume-safe from local state.
        """

        del playlist_id
        return set()

    def get_existing_destination_target_track_ids(
        self,
        target_type: str,
        target_id: str,
        track_ids: list[str],
    ) -> set[str]:
        """Return destination track IDs that already exist for a write target."""

        del track_ids
        if target_type != "playlist":
            raise ValidationFailure(
                f"{self.platform_name} does not support destination target type: {target_type}"
            )
        return self.get_destination_track_ids(target_id)

    def validate_destination_playlist(self, playlist_id: str) -> str | None:
        """Validate that an existing destination playlist can receive writes.

        Adapters that cannot check this cheaply may leave the default no-op.
        Adapters that normalize write targets may return the target to persist.
        """

        del playlist_id
        return None

    def validate_destination_target(
        self,
        target_type: str,
        target_id: str | None,
    ) -> str:
        """Validate and normalize a destination write target."""

        if target_type != "playlist":
            raise ValidationFailure(
                f"{self.platform_name} does not support destination target type: {target_type}"
            )
        if target_id is None:
            raise ValidationFailure("playlist destination target requires a playlist id")
        return self.validate_destination_playlist(target_id) or target_id

    def is_resolved_destination_target(self, target_type: str, target_id: str) -> bool:
        """Return whether a persisted target can be reused without validation."""

        del target_type, target_id
        return not self.normalizes_destination_playlist_ids

    def destination_target_ids_match(
        self,
        target_type: str,
        left: str,
        right: str,
    ) -> bool:
        """Return whether two configured/persisted target strings identify one target."""

        del target_type
        return left == right

    def destination_target_batch_size(self, target_type: str) -> int:
        """Return the preferred write batch size for a target type."""

        del target_type
        return 1

    def add_tracks_to_target(
        self,
        target_type: str,
        target_id: str,
        track_ids: list[str],
    ) -> None:
        """Append destination-platform track IDs to a write target."""

        if target_type != "playlist":
            raise ValidationFailure(
                f"{self.platform_name} does not support destination target type: {target_type}"
            )
        self.add_tracks(target_id, track_ids)


__all__ = ["BasePlatform", "PlatformCapabilities"]
