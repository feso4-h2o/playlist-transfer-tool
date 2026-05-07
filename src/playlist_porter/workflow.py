"""Mock transfer orchestration used by the Phase 4 CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.table import Table

from playlist_porter.config import PorterConfig, SpotifyConfig
from playlist_porter.matching.candidates import match_playlist
from playlist_porter.matching.status import MatchStatus
from playlist_porter.models import MatchDecision, TrackCandidate, TransferRun
from playlist_porter.persistence.exports import export_reports
from playlist_porter.persistence.repositories import (
    TransferMetrics,
    TransferRepository,
    UserOverride,
)
from playlist_porter.platforms.base import BasePlatform
from playlist_porter.platforms.mock import MockAdapter
from playlist_porter.platforms.qqmusic import QQMusicAdapter, QQMusicConfig
from playlist_porter.platforms.spotify import SpotifyAdapter

WRITABLE_AUTO_STATUSES = {
    MatchStatus.ISRC_EXACT,
    MatchStatus.METADATA_HIGH_CONFIDENCE,
}

PlatformName = Literal["mock", "spotify", "qqmusic"]


@dataclass(frozen=True)
class DryRunResult:
    """Result from a dry-run command."""

    transfer_run_id: str
    created: bool
    metrics: TransferMetrics


@dataclass(frozen=True)
class PreflightResult:
    """Validation result for a planned transfer run."""

    source_platform: str
    destination_platform: str
    dry_run: bool
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues


class PreflightError(RuntimeError):
    """Raised when a transfer cannot start safely."""

    def __init__(self, result: PreflightResult) -> None:
        self.result = result
        super().__init__("transfer preflight failed: " + "; ".join(result.issues))


@dataclass(frozen=True)
class TransferResult:
    """Result from a platform transfer orchestration run."""

    transfer_run_id: str
    created: bool
    dry_run: bool
    destination_playlist_id: str | None
    written_count: int
    skipped_count: int
    report_paths: tuple[Path, ...]
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


def create_platform_adapter(config: PorterConfig, platform: PlatformName) -> BasePlatform:
    """Create a configured platform adapter for transfer orchestration."""

    if platform == "mock":
        return create_mock_adapter(config)
    if platform == "spotify":
        return SpotifyAdapter(config.spotify or SpotifyConfig.from_env())
    if platform == "qqmusic":
        return QQMusicAdapter(config=config.qqmusic or QQMusicConfig())
    raise ValueError(f"unsupported platform: {platform}")


def run_transfer(
    config: PorterConfig,
    *,
    source_platform: PlatformName,
    destination_platform: PlatformName,
    source_playlist_id: str,
    dry_run: bool = True,
    database_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    restart: bool = False,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
) -> TransferResult:
    """Run a direction-aware transfer with matching, persistence, optional writes, and exports."""

    return run_transfer_with_adapters(
        create_platform_adapter(config, source_platform),
        create_platform_adapter(config, destination_platform),
        source_playlist_id=source_playlist_id,
        dry_run=dry_run,
        database_path=database_path or config.database_path,
        output_dir=output_dir or config.report_output_dir,
        restart=restart,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
        console=console,
    )


def execute_transfer_run(
    config: PorterConfig,
    *,
    destination_platform: PlatformName,
    transfer_run_id: str,
    database_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
) -> TransferResult:
    """Execute writes for an existing reviewed transfer run."""

    return execute_transfer_run_with_adapter(
        create_platform_adapter(config, destination_platform),
        transfer_run_id=transfer_run_id,
        database_path=database_path or config.database_path,
        output_dir=output_dir or config.report_output_dir,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
        console=console,
    )


def execute_transfer_run_with_adapter(
    destination: BasePlatform,
    *,
    transfer_run_id: str,
    database_path: str | Path,
    output_dir: str | Path,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
) -> TransferResult:
    """Execute approved writes from persisted decisions and review overrides."""

    console = console or Console()
    preflight = validate_execute_preflight(
        destination,
        database_path=database_path,
        output_dir=output_dir,
    )
    if not preflight.ok:
        raise PreflightError(preflight)

    repository = TransferRepository(database_path)
    run_record = repository.load_run(transfer_run_id)
    if run_record.destination_platform != destination.platform_name:
        raise PreflightError(
            PreflightResult(
                source_platform=run_record.source_platform,
                destination_platform=destination.platform_name,
                dry_run=False,
                issues=(
                    "persisted run destination is "
                    f"{run_record.destination_platform}, not {destination.platform_name}",
                ),
            )
        )

    destination.authenticate()
    write_result = _execute_transfer_writes(
        repository,
        destination,
        transfer_run_id,
        dry_run=False,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
    )
    report_paths = tuple(export_reports(repository, transfer_run_id, output_dir))
    repository.mark_run_completed(transfer_run_id)
    metrics = repository.load_metrics(transfer_run_id)
    render_metrics(console, metrics, title="Transfer summary")
    return TransferResult(
        transfer_run_id=transfer_run_id,
        created=False,
        dry_run=False,
        destination_playlist_id=write_result.destination_playlist_id or None,
        written_count=write_result.attempted_count,
        skipped_count=write_result.skipped_count,
        report_paths=report_paths,
        metrics=metrics,
    )


def run_transfer_with_adapters(
    source: BasePlatform,
    destination: BasePlatform,
    *,
    source_playlist_id: str,
    dry_run: bool,
    database_path: str | Path,
    output_dir: str | Path,
    restart: bool = False,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
) -> TransferResult:
    """Run the Phase 8 orchestration using already-created adapters."""

    console = console or Console()
    preflight = validate_transfer_preflight(
        source,
        destination,
        dry_run=dry_run,
        database_path=database_path,
        output_dir=output_dir,
    )
    if not preflight.ok:
        raise PreflightError(preflight)

    source.authenticate()
    if destination is not source:
        destination.authenticate()

    repository = TransferRepository(database_path)
    playlist = source.get_playlist(source_playlist_id)
    run = TransferRun(
        source_platform=source.platform_name,
        destination_platform=destination.platform_name,
        source_playlist=playlist,
        destination_playlist_id=destination_playlist_id,
        dry_run=dry_run,
        metadata={"phase": 8, "dry_run": dry_run},
    )

    if restart:
        transfer_run_id = repository.create_run(run)
        created = True
    else:
        transfer_run_id, created = repository.get_or_create_run(run)
        if not created and dry_run:
            repository.prune_transfer_state(
                transfer_run_id,
                [track.internal_id for track in playlist.tracks],
            )

    if created or dry_run:
        repository.save_source_playlist(transfer_run_id, playlist)
        decisions = match_playlist(playlist, destination)
        repository.save_match_decisions(transfer_run_id, decisions)
    else:
        repository.sync_metrics(transfer_run_id)

    write_result = _execute_transfer_writes(
        repository,
        destination,
        transfer_run_id,
        dry_run=dry_run,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
    )

    repository.sync_metrics(transfer_run_id)
    report_paths = tuple(export_reports(repository, transfer_run_id, output_dir))
    if not dry_run:
        repository.mark_run_completed(transfer_run_id)

    metrics = repository.load_metrics(transfer_run_id)
    render_metrics(
        console,
        metrics,
        title="Dry run summary" if dry_run else "Transfer summary",
    )
    return TransferResult(
        transfer_run_id=transfer_run_id,
        created=created,
        dry_run=dry_run,
        destination_playlist_id=write_result.destination_playlist_id or None,
        written_count=write_result.attempted_count,
        skipped_count=write_result.skipped_count,
        report_paths=report_paths,
        metrics=metrics,
    )


def validate_transfer_preflight(
    source: BasePlatform,
    destination: BasePlatform,
    *,
    dry_run: bool,
    database_path: str | Path,
    output_dir: str | Path,
) -> PreflightResult:
    """Check capabilities, credentials, and local write targets before a transfer."""

    issues: list[str] = []
    if not source.capabilities.supports_read:
        issues.append(f"{source.platform_name} cannot be used as a readable source")
    if not destination.capabilities.supports_search:
        issues.append(f"{destination.platform_name} cannot be searched as a destination")
    if not dry_run and not destination.capabilities.supports_write:
        issues.append(f"{destination.platform_name} cannot write destination playlists")

    issues.extend(_credential_issues(source))
    if destination is not source:
        issues.extend(_credential_issues(destination))

    issues.extend(_writable_path_issues(database_path, label="database path"))
    issues.extend(
        _writable_path_issues(output_dir, label="report output directory", directory=True)
    )

    return PreflightResult(
        source_platform=source.platform_name,
        destination_platform=destination.platform_name,
        dry_run=dry_run,
        issues=tuple(issues),
    )


def validate_execute_preflight(
    destination: BasePlatform,
    *,
    database_path: str | Path,
    output_dir: str | Path,
) -> PreflightResult:
    """Check local state and destination write readiness for a reviewed run."""

    issues: list[str] = []
    if not destination.capabilities.supports_write:
        issues.append(f"{destination.platform_name} cannot write destination playlists")
    issues.extend(_credential_issues(destination))
    issues.extend(_writable_path_issues(database_path, label="database path"))
    issues.extend(
        _writable_path_issues(output_dir, label="report output directory", directory=True)
    )
    return PreflightResult(
        source_platform="persisted",
        destination_platform=destination.platform_name,
        dry_run=False,
        issues=tuple(issues),
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


def _execute_transfer_writes(
    repository: TransferRepository,
    destination: BasePlatform,
    transfer_run_id: str,
    *,
    dry_run: bool,
    destination_playlist_id: str | None,
    create_playlist_name: str | None,
) -> ExecuteResult:
    if dry_run:
        write_pairs = _eligible_write_pairs(
            repository.load_match_decisions(transfer_run_id),
            repository.load_user_overrides(transfer_run_id),
        )
        return ExecuteResult(
            transfer_run_id=transfer_run_id,
            destination_playlist_id=destination_playlist_id or "",
            attempted_count=0,
            skipped_count=len(write_pairs),
            metrics=repository.load_metrics(transfer_run_id),
    )

    run_record = repository.load_run(transfer_run_id)
    if (
        destination_playlist_id is not None
        and run_record.destination_playlist_id is not None
        and destination_playlist_id != run_record.destination_playlist_id
    ):
        raise ValueError(
            "transfer run already targets destination playlist "
            f"{run_record.destination_playlist_id}; restart the transfer to use "
            f"{destination_playlist_id}"
        )

    destination_id = destination_playlist_id or run_record.destination_playlist_id
    if destination_playlist_id is not None:
        repository.update_destination_playlist_id(transfer_run_id, destination_playlist_id)
    if destination_id is None:
        destination_id = destination.create_playlist(
            create_playlist_name or f"{run_record.source_playlist_name or 'Playlist'} Copy",
            "Created by playlist-porter transfer execution",
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
    written_count = _write_pending_pairs(
        destination,
        destination_id,
        pending_pairs,
        repository=repository,
        transfer_run_id=transfer_run_id,
    )

    return ExecuteResult(
        transfer_run_id=transfer_run_id,
        destination_playlist_id=destination_id,
        attempted_count=written_count,
        skipped_count=len(write_pairs) - len(pending_pairs),
        metrics=repository.load_metrics(transfer_run_id),
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


def _write_pending_pairs(
    destination: BasePlatform,
    destination_playlist_id: str,
    pending_pairs: list[tuple[str, str]],
    *,
    repository: TransferRepository,
    transfer_run_id: str,
) -> int:
    if not pending_pairs:
        return 0

    progress_writer = getattr(destination, "add_tracks_with_progress", None)
    if callable(progress_writer):
        try:
            return int(
                progress_writer(
                    destination_playlist_id,
                    [source_track_id for source_track_id, _ in pending_pairs],
                    [track_id for _, track_id in pending_pairs],
                    repository=repository,
                    transfer_run_id=transfer_run_id,
                )
            )
        except Exception as exc:
            _record_first_incomplete_write_failure(
                pending_pairs,
                repository=repository,
                transfer_run_id=transfer_run_id,
                error=str(exc) or exc.__class__.__name__,
            )
            raise

    written_count = 0
    for source_track_id, track_id in pending_pairs:
        try:
            destination.add_tracks(destination_playlist_id, [track_id])
        except Exception as exc:
            repository.record_write_failure(
                transfer_run_id,
                source_track_id,
                track_id,
                error=str(exc) or exc.__class__.__name__,
            )
            raise
        repository.record_write_success(transfer_run_id, source_track_id, track_id)
        written_count += 1
    return written_count


def _record_first_incomplete_write_failure(
    pending_pairs: list[tuple[str, str]],
    *,
    repository: TransferRepository,
    transfer_run_id: str,
    error: str,
) -> None:
    for source_track_id, track_id in pending_pairs:
        if repository.should_write_track(transfer_run_id, source_track_id, track_id):
            repository.record_write_failure(
                transfer_run_id,
                source_track_id,
                track_id,
                error=error,
            )
            return


def _credential_issues(adapter: BasePlatform) -> list[str]:
    if isinstance(adapter, SpotifyAdapter):
        missing = adapter.config.missing_credentials()
        if missing and getattr(adapter, "_client", None) is None:
            return [
                "Spotify credentials are missing: "
                + ", ".join(f"SPOTIFY_{field.upper()}" for field in missing)
            ]
    if isinstance(adapter, QQMusicAdapter):
        if getattr(adapter, "_client", None) is not None:
            return []
        try:
            credential_payload = adapter.config.load_credential_payload()
        except FileNotFoundError as exc:
            return [f"QQ Music credential file is missing: {exc.filename}"]
        if credential_payload is None:
            return ["QQ Music credentials are missing: configure qqmusic.credential_path"]
    return []


def _writable_path_issues(
    path: str | Path,
    *,
    label: str,
    directory: bool = False,
) -> list[str]:
    target = Path(path)
    check_dir = target if directory else target.parent
    try:
        check_dir.mkdir(parents=True, exist_ok=True)
        probe = check_dir / ".playlist-porter-write-check"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return [f"{label} is not writable: {exc}"]
    return []


__all__ = [
    "DryRunResult",
    "ExecuteResult",
    "PlatformName",
    "PreflightError",
    "PreflightResult",
    "TransferResult",
    "create_mock_adapter",
    "create_platform_adapter",
    "dry_run_mock_transfer",
    "execute_transfer_run",
    "execute_transfer_run_with_adapter",
    "execute_mock_transfer",
    "run_transfer",
    "run_transfer_with_adapters",
    "render_metrics",
    "validate_execute_preflight",
    "validate_transfer_preflight",
]
