import json

import pytest

from playlist_porter.config import PorterConfig, SpotifyConfig
from playlist_porter.models import Playlist, TrackCandidate, UniversalTrack
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.platforms.base import BasePlatform, PlatformCapabilities
from playlist_porter.platforms.mock import MockAdapter
from playlist_porter.platforms.spotify import SpotifyAdapter
from playlist_porter.workflow import (
    PreflightError,
    run_transfer,
    run_transfer_with_adapters,
    validate_transfer_preflight,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _config(tmp_path, *, playlist_tracks, catalog_tracks) -> PorterConfig:
    playlists_path = tmp_path / "fixtures" / "playlists.json"
    catalog_path = tmp_path / "fixtures" / "catalog.json"
    _write_json(
        playlists_path,
        {
            "playlists": [
                {
                    "id": "source-playlist",
                    "name": "Source",
                    "tracks": playlist_tracks,
                }
            ]
        },
    )
    _write_json(catalog_path, {"catalog": catalog_tracks})
    return PorterConfig(
        database_path=tmp_path / "state" / "transfer.sqlite",
        report_output_dir=tmp_path / "reports",
        mock_source_playlists_path=playlists_path,
        mock_destination_catalog_path=catalog_path,
        mock_writes_path=tmp_path / "state" / "writes.json",
    )


def _phase8_config(tmp_path) -> PorterConfig:
    return _config(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-exact",
                "title": "Alpha",
                "artists": ["Exact Artist"],
                "duration_seconds": 180,
                "isrc": "ISRC1",
            },
            {
                "id": "source-review",
                "title": "Beta",
                "artists": ["Review Artist"],
                "duration_seconds": 180,
            },
            {
                "id": "source-missing",
                "title": "No Destination",
                "artists": ["Missing Artist"],
                "duration_seconds": 180,
            },
        ],
        catalog_tracks=[
            {
                "id": "dest-exact",
                "title": "Alpha",
                "artists": ["Exact Artist"],
                "duration_seconds": 180,
                "isrc": "ISRC1",
            },
            {
                "id": "dest-review",
                "title": "Beta (Remix)",
                "artists": ["Review Artist"],
                "duration_seconds": 180,
            },
        ],
    )


def test_phase8_mock_dry_run_exports_summary_matching_metrics(tmp_path) -> None:
    config = _phase8_config(tmp_path)

    result = run_transfer(
        config,
        source_platform="mock",
        destination_platform="mock",
        source_playlist_id="source-playlist",
        dry_run=True,
    )

    summary = json.loads((config.report_output_dir / "transfer-summary.json").read_text())
    unavailable = json.loads((config.report_output_dir / "unavailable-tracks.json").read_text())

    assert result.dry_run is True
    assert result.written_count == 0
    assert result.metrics.source_track_count == 3
    assert summary["transfer_run_id"] == result.transfer_run_id
    assert summary["source_track_count"] == result.metrics.source_track_count
    assert summary["candidate_count"] == result.metrics.candidate_count
    assert {row["status"] for row in unavailable} == {"needs_review", "not_found"}


def test_phase8_write_mode_writes_only_auto_accepted_tracks(tmp_path) -> None:
    config = _phase8_config(tmp_path)

    result = run_transfer(
        config,
        source_platform="mock",
        destination_platform="mock",
        source_playlist_id="source-playlist",
        dry_run=False,
        create_playlist_name="Copied",
    )

    writes = json.loads(config.mock_writes_path.read_text(encoding="utf-8"))
    assert result.written_count == 1
    assert result.skipped_count == 0
    assert result.metrics.write_success_count == 1
    assert writes[result.destination_playlist_id]["track_ids"] == ["dest-exact"]


def test_phase8_resume_skips_recorded_writes(tmp_path) -> None:
    config = _phase8_config(tmp_path)
    first = run_transfer(
        config,
        source_platform="mock",
        destination_platform="mock",
        source_playlist_id="source-playlist",
        dry_run=False,
        destination_playlist_id="existing-playlist",
    )

    second = run_transfer(
        config,
        source_platform="mock",
        destination_platform="mock",
        source_playlist_id="source-playlist",
        dry_run=False,
        destination_playlist_id="existing-playlist",
    )

    writes = json.loads(config.mock_writes_path.read_text(encoding="utf-8"))
    assert first.transfer_run_id == second.transfer_run_id
    assert second.written_count == 0
    assert second.skipped_count == 1
    assert writes["existing-playlist"]["track_ids"] == ["dest-exact"]
    assert TransferRepository(config.database_path).load_metrics(
        first.transfer_run_id
    ).write_success_count == 1


def test_phase8_preflight_rejects_non_writable_destination_for_write(tmp_path) -> None:
    source = StaticSourceAdapter()
    destination = SearchOnlyDestinationAdapter()

    result = validate_transfer_preflight(
        source,
        destination,
        dry_run=False,
        database_path=tmp_path / "transfer.sqlite",
        output_dir=tmp_path / "reports",
    )

    assert result.ok is False
    assert result.issues == ("search-only cannot write destination playlists",)
    with pytest.raises(PreflightError):
        run_transfer_with_adapters(
            source,
            destination,
            source_playlist_id="source-playlist",
            dry_run=False,
            database_path=tmp_path / "transfer.sqlite",
            output_dir=tmp_path / "reports",
        )


def test_phase8_preflight_reports_missing_spotify_credentials(tmp_path) -> None:
    source = MockAdapter(
        playlists={"source-playlist": Playlist(name="Source", tracks=[])},
        catalog=[],
    )
    destination = SpotifyAdapter(SpotifyConfig())

    result = validate_transfer_preflight(
        source,
        destination,
        dry_run=True,
        database_path=tmp_path / "transfer.sqlite",
        output_dir=tmp_path / "reports",
    )

    assert result.ok is False
    assert result.issues == (
        "Spotify credentials are missing: SPOTIFY_CLIENT_ID, "
        "SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI",
    )


class StaticSourceAdapter(BasePlatform):
    platform_name = "static-source"
    capabilities = PlatformCapabilities(supports_read=True)

    def authenticate(self) -> None:
        return None

    def get_playlist(self, playlist_id_or_url: str) -> Playlist:
        assert playlist_id_or_url == "source-playlist"
        return Playlist(
            name="Source",
            platform="static-source",
            platform_playlist_id="source-playlist",
            tracks=[UniversalTrack(title="Alpha", artists=["Artist"], platform="static-source")],
        )

    def search_tracks(self, query: str, limit: int = 10) -> list[TrackCandidate]:
        del query, limit
        return []

    def create_playlist(self, name: str, description: str | None = None) -> str:
        del name, description
        return "unused"

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        del playlist_id, track_ids


class SearchOnlyDestinationAdapter(BasePlatform):
    platform_name = "search-only"
    capabilities = PlatformCapabilities(
        supports_read=False,
        supports_search=True,
        supports_write=False,
    )

    def authenticate(self) -> None:
        return None

    def get_playlist(self, playlist_id_or_url: str) -> Playlist:
        del playlist_id_or_url
        raise NotImplementedError

    def search_tracks(self, query: str, limit: int = 10) -> list[TrackCandidate]:
        del query, limit
        return []

    def create_playlist(self, name: str, description: str | None = None) -> str:
        del name, description
        raise NotImplementedError

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        del playlist_id, track_ids
        raise NotImplementedError
