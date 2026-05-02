"""Mock transfer orchestration used by the Phase 4 CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from playlist_porter.config import PorterConfig
from playlist_porter.matching.candidates import match_playlist
from playlist_porter.matching.status import MatchStatus
from playlist_porter.models import MatchDecision, TrackCandidate, TransferRun
from playlist_porter.persistence.repositories import (
    TransferMetrics,
    TransferRepository,
    UserOverride,
)
from playlist_porter.platforms.mock import MockAdapter

WRITABLE_AUTO_STATUSES = {
    MatchStatus.ISRC_EXACT,
    MatchStatus.METADATA_HIGH_CONFIDENCE,
}


@dataclass(frozen=True)
class DryRunResult:
    """Result from a dry-run command."""

    transfer_run_id: str
    created: bool
    metrics: TransferMetrics


@dataclass(frozen=True)
class ExecuteResult:
    """Result from an execute or resume command."""

    transfer_run_id: str
    destination_playlist_id: str
    attempted_count: int
    skipped_count: int
    metrics: TransferMetrics


def create_mock_adapter(config: PorterConfig) -> MockAdapter:
    """Create the fixture-backed adapter used for Phase 4 commands."""

    return MockAdapter.from_json(
        playlists_path=config.mock_source_playlists_path,
        catalog_path=config.mock_destination_catalog_path,
        writes_path=config.mock_writes_path,
    )


def dry_run_mock_transfer(
    config: PorterConfig,
    *,
    source_playlist_id: str,
    database_path: str | Path | None = None,
    restart: bool = False,
    console: Console | None = None,
) -> DryRunResult:
    """Run mock matching, persist decisions, and perform no destination writes."""

    console = console or Console()
    repository = TransferRepository(database_path or config.database_path)
    adapter = create_mock_adapter(config)
    adapter.authenticate()
    playlist = adapter.get_playlist(source_playlist_id)
    run = TransferRun(
        source_platform="mock",
        destination_platform="mock",
        source_playlist=playlist,
        dry_run=True,
    )

    if restart:
        transfer_run_id = repository.create_run(run)
        created = True
    else:
        transfer_run_id, created = repository.get_or_create_run(run)
        if not created:
            repository.prune_transfer_state(
                transfer_run_id,
                [track.internal_id for track in playlist.tracks],
            )

    repository.save_source_playlist(transfer_run_id, playlist)
    decisions = match_playlist(playlist, adapter)
    repository.save_match_decisions(transfer_run_id, decisions)
    metrics = repository.load_metrics(transfer_run_id)
    render_metrics(console, metrics, title="Dry run summary")
    return DryRunResult(transfer_run_id=transfer_run_id, created=created, metrics=metrics)


def execute_mock_transfer(
    config: PorterConfig,
    *,
    transfer_run_id: str,
    database_path: str | Path | None = None,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
) -> ExecuteResult:
    """Write eligible mock tracks while preserving resume semantics."""

    console = console or Console()
    repository = TransferRepository(database_path or config.database_path)
    adapter = create_mock_adapter(config)
    adapter.authenticate()
    run_record = repository.load_run(transfer_run_id)
    destination_id = destination_playlist_id or run_record.destination_playlist_id
    if destination_playlist_id is not None:
        repository.update_destination_playlist_id(transfer_run_id, destination_playlist_id)
    if destination_id is None:
        destination_id = adapter.create_playlist(
            create_playlist_name or f"{run_record.source_playlist_name or 'Playlist'} Copy",
            "Created by playlist-porter mock execution",
        )
        repository.update_destination_playlist_id(transfer_run_id, destination_id)

    write_pairs = _eligible_write_pairs(
        repository.load_match_decisions(transfer_run_id),
        repository.load_user_overrides(transfer_run_id),
    )
    pending_destination_ids = repository.pending_write_track_ids(
        transfer_run_id,
        [pair[1] for pair in write_pairs],
        source_track_ids=[pair[0] for pair in write_pairs],
    )
    pending_pairs = _pending_pairs(
        write_pairs,
        pending_destination_ids,
        repository,
        transfer_run_id,
    )

    if pending_pairs:
        adapter.add_tracks(destination_id, [destination_id for _, destination_id in pending_pairs])
        for source_track_id, track_id in pending_pairs:
            repository.record_write_success(transfer_run_id, source_track_id, track_id)

    repository.mark_run_completed(transfer_run_id)
    metrics = repository.load_metrics(transfer_run_id)
    render_metrics(console, metrics, title="Execution summary")
    return ExecuteResult(
        transfer_run_id=transfer_run_id,
        destination_playlist_id=destination_id,
        attempted_count=len(pending_pairs),
        skipped_count=len(write_pairs) - len(pending_pairs),
        metrics=metrics,
    )


def render_metrics(console: Console, metrics: TransferMetrics, *, title: str) -> None:
    """Print a compact metrics table."""

    table = Table(title=title)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for label, value in [
        ("run id", metrics.transfer_run_id),
        ("source tracks", metrics.source_track_count),
        ("candidates", metrics.candidate_count),
        ("auto accepted", metrics.auto_accepted_count),
        ("review required", metrics.review_required_count),
        ("not found", metrics.not_found_count),
        ("user approved", metrics.user_approved_count),
        ("user rejected", metrics.user_rejected_count),
        ("write successes", metrics.write_success_count),
        ("write failures", metrics.write_failure_count),
    ]:
        table.add_row(label, str(value))
    console.print(table)


def _eligible_write_pairs(
    decisions: list[MatchDecision],
    overrides: dict[str, UserOverride],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for decision in decisions:
        source_track_id = str(decision.source_track.internal_id)
        candidate = _effective_selected_candidate(decision, overrides.get(source_track_id))
        if candidate is None or candidate.track.platform_track_id is None:
            continue
        pairs.append((source_track_id, candidate.track.platform_track_id))
    return pairs


def _effective_selected_candidate(
    decision: MatchDecision,
    override: UserOverride | None,
) -> TrackCandidate | None:
    if override is not None:
        if override.status is MatchStatus.USER_REJECTED:
            return None
        if override.status is MatchStatus.USER_APPROVED:
            return _candidate_by_internal_id(decision, override.selected_candidate_internal_id)
    if decision.status in WRITABLE_AUTO_STATUSES:
        return decision.selected_candidate
    return None


def _candidate_by_internal_id(
    decision: MatchDecision,
    candidate_internal_id: str | None,
) -> TrackCandidate | None:
    if candidate_internal_id is None:
        return None
    for candidate in decision.candidates:
        if str(candidate.track.internal_id) == candidate_internal_id:
            return candidate
    return None


def _pending_pairs(
    write_pairs: list[tuple[str, str]],
    pending_destination_ids: list[str],
    repository: TransferRepository,
    transfer_run_id: str,
) -> list[tuple[str, str]]:
    pending_pairs = [
        pair
        for pair in write_pairs
        if repository.should_write_track(transfer_run_id, pair[0], pair[1])
    ]
    if [destination_id for _, destination_id in pending_pairs] != pending_destination_ids:
        raise RuntimeError("pending write filter returned inconsistent source-aware results")
    return pending_pairs


__all__ = [
    "DryRunResult",
    "ExecuteResult",
    "create_mock_adapter",
    "dry_run_mock_transfer",
    "execute_mock_transfer",
    "render_metrics",
]
