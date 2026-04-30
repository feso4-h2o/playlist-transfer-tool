"""Base platform adapter contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from playlist_porter.models import Playlist, TrackCandidate


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


__all__ = ["BasePlatform", "PlatformCapabilities"]
