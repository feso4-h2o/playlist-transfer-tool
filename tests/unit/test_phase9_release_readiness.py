from pathlib import Path

from playlist_porter.config import PorterConfig
from playlist_porter.matching.status import MatchStatus
from playlist_porter.workflow import run_transfer


def test_tracked_mock_fixtures_support_credential_free_dry_run(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = PorterConfig(
        database_path=tmp_path / "state" / "playlist-porter.sqlite",
        report_output_dir=tmp_path / "reports",
        mock_source_playlists_path=repo_root / "fixtures" / "mock-playlists.json",
        mock_destination_catalog_path=repo_root / "fixtures" / "mock-catalog.json",
        mock_writes_path=tmp_path / "state" / "mock-writes.json",
    )

    result = run_transfer(
        config,
        source_platform="mock",
        destination_platform="mock",
        source_playlist_id="sample-mixed",
        dry_run=True,
    )

    assert result.written_count == 0
    assert result.metrics.source_track_count == 4
    assert result.metrics.status_counts[MatchStatus.ISRC_EXACT.value] == 1
    assert result.metrics.status_counts[MatchStatus.NEEDS_REVIEW.value] == 1
    assert result.metrics.status_counts[MatchStatus.NOT_FOUND.value] == 2
    report_dir = config.report_output_dir / result.transfer_run_id[:8]
    assert list(report_dir.glob("transfer-summary-*.json"))
    assert list(report_dir.glob("unavailable-tracks-*.csv"))
