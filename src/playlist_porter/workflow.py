"""Mock transfer orchestration used by the Phase 4 CLI."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import Literal

from loguru import logger
from rich.console import Console
from rich.table import Table

from playlist_porter.config import PorterConfig, SpotifyConfig
from playlist_porter.diagnostics import (
    candidate_summary,
    diagnostic_logger,
    metrics_snapshot,
    platform_capabilities_snapshot,
    preflight_snapshot,
    track_summary,
    write_pair_snapshot,
)
from playlist_porter.matching.candidates import match_playlist
from playlist_porter.matching.status import MatchStatus
from playlist_porter.models import MatchDecision, TrackCandidate, TransferRun
from playlist_porter.persistence.exports import export_reports
from playlist_porter.persistence.repositories import (
    WRITE_SKIP_EXISTING_STEP,
    WRITE_SKIP_RESUME_STEP,
    TransferMetrics,
    TransferRepository,
    UserOverride,
)
from playlist_porter.platforms.base import BasePlatform
from playlist_porter.platforms.mock import MockAdapter
from playlist_porter.platforms.qqmusic import QQMusicAdapter, QQMusicConfig
from playlist_porter.platforms.spotify import SpotifyAdapter
from playlist_porter.progress import ProgressReporter, report_progress

WRITABLE_AUTO_STATUSES = {
    MatchStatus.ISRC_EXACT,
    MatchStatus.METADATA_HIGH_CONFIDENCE,
}

PlatformName = Literal["mock", "spotify", "qqmusic"]
WORKFLOW_DIAGNOSTICS = diagnostic_logger("workflow")
WRITE_DIAGNOSTICS = diagnostic_logger("write")


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


@dataclass(frozen=True)
class WritePair:
    """One source-to-destination pair eligible for writes."""

    source_track_id: str
    track_id: str
    source_title: str


def create_mock_adapter(config: PorterConfig) -> MockAdapter:
    """Create the fixture-backed adapter used for Phase 4 commands."""

    logger.debug(
        "creating mock adapter",
        source_playlists_path=str(config.mock_source_playlists_path),
        destination_catalog_path=str(config.mock_destination_catalog_path),
        writes_path=str(config.mock_writes_path) if config.mock_writes_path else None,
    )
    return MockAdapter.from_json(
        playlists_path=config.mock_source_playlists_path,
        catalog_path=config.mock_destination_catalog_path,
        writes_path=config.mock_writes_path,
    )


def create_platform_adapter(config: PorterConfig, platform: PlatformName) -> BasePlatform:
    """Create a configured platform adapter for transfer orchestration."""

    logger.debug("creating platform adapter", platform=platform)
    if platform == "mock":
        return create_mock_adapter(config)
    if platform == "spotify":
        return SpotifyAdapter(config.spotify or SpotifyConfig.from_env())
    if platform == "qqmusic":
        return QQMusicAdapter(config=config.qqmusic or QQMusicConfig.from_env())
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
    output_format: str | None = None,
    restart: bool = False,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> TransferResult:
    """Run a direction-aware transfer with matching, persistence, optional writes, and exports."""

    logger.info(
        "transfer run requested",
        source_platform=source_platform,
        destination_platform=destination_platform,
        dry_run=dry_run,
        database_path=str(database_path or config.database_path),
        output_dir=str(output_dir or config.report_output_dir),
        restart=restart,
    )
    return run_transfer_with_adapters(
        create_platform_adapter(config, source_platform),
        create_platform_adapter(config, destination_platform),
        source_playlist_id=source_playlist_id,
        dry_run=dry_run,
        database_path=database_path or config.database_path,
        output_dir=output_dir or config.report_output_dir,
        output_format=output_format or config.report_format,
        restart=restart,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
        console=console,
        progress_reporter=progress_reporter,
    )


def execute_transfer_run(
    config: PorterConfig,
    *,
    destination_platform: PlatformName,
    transfer_run_id: str,
    database_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    output_format: str | None = None,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> TransferResult:
    """Execute writes for an existing reviewed transfer run."""

    logger.info(
        "write run requested",
        destination_platform=destination_platform,
        run_id=transfer_run_id,
        database_path=str(database_path or config.database_path),
        output_dir=str(output_dir or config.report_output_dir),
    )
    return execute_transfer_run_with_adapter(
        create_platform_adapter(config, destination_platform),
        transfer_run_id=transfer_run_id,
        database_path=database_path or config.database_path,
        output_dir=output_dir or config.report_output_dir,
        output_format=output_format or config.report_format,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
        console=console,
        progress_reporter=progress_reporter,
    )


def execute_transfer_run_with_adapter(
    destination: BasePlatform,
    *,
    transfer_run_id: str,
    database_path: str | Path,
    output_dir: str | Path,
    output_format: str = "json",
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> TransferResult:
    """Execute approved writes from persisted decisions and review overrides."""

    console = console or Console()
    preflight = validate_execute_preflight(
        destination,
        database_path=database_path,
        output_dir=output_dir,
    )
    if not preflight.ok:
        logger.error("execute preflight failed", issues=list(preflight.issues))
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
    logger.info("destination authenticated", destination_platform=destination.platform_name)
    write_result = _execute_transfer_writes(
        repository,
        destination,
        transfer_run_id,
        dry_run=False,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
        progress_reporter=progress_reporter,
    )
    report_paths = tuple(
        export_reports(
            repository,
            transfer_run_id,
            output_dir,
            output_format=output_format,
            command="write",
        )
    )
    logger.info(
        "reports exported",
        run_id=transfer_run_id,
        reports=[str(path) for path in report_paths],
    )
    repository.mark_run_completed(transfer_run_id)
    metrics = repository.load_metrics(transfer_run_id)
    WORKFLOW_DIAGNOSTICS.debug(
        "execute metrics loaded",
        run_id=transfer_run_id,
        metrics=metrics_snapshot(metrics),
    )
    _finish_progress(progress_reporter)
    render_metrics(console, metrics, title="Write summary")
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
    output_format: str = "json",
    restart: bool = False,
    destination_playlist_id: str | None = None,
    create_playlist_name: str | None = None,
    console: Console | None = None,
    progress_reporter: ProgressReporter | None = None,
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
        logger.error("transfer preflight failed", issues=list(preflight.issues))
        raise PreflightError(preflight)

    source.authenticate()
    logger.info("source authenticated", source_platform=source.platform_name)
    if destination is not source:
        destination.authenticate()
        logger.info("destination authenticated", destination_platform=destination.platform_name)

    repository = TransferRepository(database_path)
    playlist = source.get_playlist(source_playlist_id)
    logger.info(
        "source playlist loaded",
        source_platform=source.platform_name,
        track_count=len(playlist.tracks),
    )
    WORKFLOW_DIAGNOSTICS.debug(
        "source playlist loaded",
        source_platform=source.platform_name,
        playlist_name=playlist.name,
        playlist_platform=playlist.platform,
        playlist_id=playlist.platform_playlist_id,
        track_count=len(playlist.tracks),
    )
    for track in playlist.tracks:
        WORKFLOW_DIAGNOSTICS.debug(
            "source playlist track loaded",
            source_platform=source.platform_name,
            track=track_summary(track),
        )
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
        logger.info("transfer run created", run_id=transfer_run_id, restart=True)
    else:
        transfer_run_id, created = repository.get_or_create_run(run)
        logger.info("transfer run resolved", run_id=transfer_run_id, created=created)
        if not created and dry_run:
            logger.info("pruning stale dry-run transfer state", run_id=transfer_run_id)
            repository.prune_transfer_state(
                transfer_run_id,
                [track.internal_id for track in playlist.tracks],
            )

    WORKFLOW_DIAGNOSTICS.debug(
        "transfer run state resolved",
        run_id=transfer_run_id,
        created=created,
        restart=restart,
        dry_run=dry_run,
        source_platform=source.platform_name,
        destination_platform=destination.platform_name,
    )

    if created or dry_run:
        repository.save_source_playlist(transfer_run_id, playlist)
        decisions = match_playlist(
            playlist,
            destination,
            progress_reporter=progress_reporter,
        )
        logger.info(
            "match decisions generated",
            run_id=transfer_run_id,
            decision_count=len(decisions),
            candidate_count=sum(len(decision.candidates) for decision in decisions),
        )
        WORKFLOW_DIAGNOSTICS.debug(
            "match decisions generated",
            run_id=transfer_run_id,
            decision_count=len(decisions),
            candidate_count=sum(len(decision.candidates) for decision in decisions),
        )
        repository.save_match_decisions(transfer_run_id, decisions)
    else:
        logger.info("syncing metrics for existing run", run_id=transfer_run_id)
        repository.sync_metrics(transfer_run_id)

    write_result = _execute_transfer_writes(
        repository,
        destination,
        transfer_run_id,
        dry_run=dry_run,
        destination_playlist_id=destination_playlist_id,
        create_playlist_name=create_playlist_name,
        progress_reporter=progress_reporter,
    )

    repository.sync_metrics(transfer_run_id)
    report_paths = tuple(
        export_reports(
            repository,
            transfer_run_id,
            output_dir,
            output_format=output_format,
            command="match" if dry_run else "write",
        )
    )
    logger.info(
        "reports exported",
        run_id=transfer_run_id,
        reports=[str(path) for path in report_paths],
    )
    if not dry_run:
        repository.mark_run_completed(transfer_run_id)

    metrics = repository.load_metrics(transfer_run_id)
    WORKFLOW_DIAGNOSTICS.debug(
        "transfer metrics loaded",
        run_id=transfer_run_id,
        metrics=metrics_snapshot(metrics),
    )
    _finish_progress(progress_reporter)
    render_metrics(
        console,
        metrics,
        title="Match summary" if dry_run else "Write summary",
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

    issues.extend(_credential_issues(source, require_write=False, require_playlist_read=True))
    if destination is source:
        if not dry_run:
            issues.extend(_credential_issues(destination, require_write=True))
    else:
        issues.extend(_credential_issues(destination, require_write=not dry_run))

    issues.extend(_writable_path_issues(database_path, label="database path"))
    issues.extend(
        _writable_path_issues(output_dir, label="report output directory", directory=True)
    )

    result = PreflightResult(
        source_platform=source.platform_name,
        destination_platform=destination.platform_name,
        dry_run=dry_run,
        issues=tuple(issues),
    )
    WORKFLOW_DIAGNOSTICS.debug(
        "transfer preflight checked",
        preflight=preflight_snapshot(result),
        source=platform_capabilities_snapshot(source),
        destination=platform_capabilities_snapshot(destination),
        database_path=str(database_path),
        output_dir=str(output_dir),
    )
    return result


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
    issues.extend(_credential_issues(destination, require_write=True))
    issues.extend(_writable_path_issues(database_path, label="database path"))
    issues.extend(
        _writable_path_issues(output_dir, label="report output directory", directory=True)
    )
    result = PreflightResult(
        source_platform="persisted",
        destination_platform=destination.platform_name,
        dry_run=False,
        issues=tuple(issues),
    )
    WORKFLOW_DIAGNOSTICS.debug(
        "execute preflight checked",
        preflight=preflight_snapshot(result),
        destination=platform_capabilities_snapshot(destination),
        database_path=str(database_path),
        output_dir=str(output_dir),
    )
    return result


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
    logger.info("mock write adapter authenticated")
    destination_id = _resolve_destination_write_target(
        repository,
        adapter,
        transfer_run_id,
        destination_playlist_id=_optional_text(destination_playlist_id),
        create_playlist_name=_optional_text(create_playlist_name),
        default_create_name_suffix="mock execution",
    )

    write_pairs = _eligible_write_pairs(
        repository.load_match_decisions(transfer_run_id),
        repository.load_user_overrides(transfer_run_id),
    )
    pending_pairs = _pending_pairs(
        write_pairs,
        repository,
        transfer_run_id,
    )
    write_ready_pairs = _filter_destination_duplicate_pairs(
        adapter,
        destination_id,
        pending_pairs,
        repository=repository,
        transfer_run_id=transfer_run_id,
    )

    written_count = _write_pending_pairs(
        adapter,
        destination_id,
        write_ready_pairs,
        repository=repository,
        transfer_run_id=transfer_run_id,
    )

    repository.mark_run_completed(transfer_run_id)
    metrics = repository.load_metrics(transfer_run_id)
    WORKFLOW_DIAGNOSTICS.debug(
        "mock execution metrics loaded",
        run_id=transfer_run_id,
        metrics=metrics_snapshot(metrics),
    )
    render_metrics(console, metrics, title="Execution summary")
    return ExecuteResult(
        transfer_run_id=transfer_run_id,
        destination_playlist_id=destination_id,
        attempted_count=written_count,
        skipped_count=len(write_pairs) - written_count,
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
    progress_reporter: ProgressReporter | None = None,
) -> ExecuteResult:
    if dry_run:
        write_pairs = _eligible_write_pairs(
            repository.load_match_decisions(transfer_run_id),
            repository.load_user_overrides(transfer_run_id),
        )
        logger.info(
            "dry run skipped destination writes",
            run_id=transfer_run_id,
            eligible_write_count=len(write_pairs),
        )
        return ExecuteResult(
            transfer_run_id=transfer_run_id,
            destination_playlist_id=destination_playlist_id or "",
            attempted_count=0,
            skipped_count=len(write_pairs),
            metrics=repository.load_metrics(transfer_run_id),
        )

    destination_id = _resolve_destination_write_target(
        repository,
        destination,
        transfer_run_id,
        destination_playlist_id=_optional_text(destination_playlist_id),
        create_playlist_name=_optional_text(create_playlist_name),
        default_create_name_suffix="write",
    )

    write_pairs = _eligible_write_pairs(
        repository.load_match_decisions(transfer_run_id),
        repository.load_user_overrides(transfer_run_id),
    )
    pending_pairs = _pending_pairs(
        write_pairs,
        repository,
        transfer_run_id,
    )
    if not pending_pairs:
        _report_no_write_progress(
            progress_reporter,
            label="No pending tracks to write",
        )
    write_ready_pairs = (
        _filter_destination_duplicate_pairs(
            destination,
            destination_id,
            pending_pairs,
            repository=repository,
            transfer_run_id=transfer_run_id,
        )
        if pending_pairs
        else []
    )
    if pending_pairs and not write_ready_pairs:
        _report_no_write_progress(
            progress_reporter,
            label="No new tracks to write",
        )
    logger.info(
        "pending writes resolved",
        run_id=transfer_run_id,
        eligible_write_count=len(write_pairs),
        pending_write_count=len(pending_pairs),
        write_ready_count=len(write_ready_pairs),
    )
    written_count = _write_pending_pairs(
        destination,
        destination_id,
        write_ready_pairs,
        repository=repository,
        transfer_run_id=transfer_run_id,
        progress_reporter=progress_reporter,
    )

    return ExecuteResult(
        transfer_run_id=transfer_run_id,
        destination_playlist_id=destination_id,
        attempted_count=written_count,
        skipped_count=len(write_pairs) - written_count,
        metrics=repository.load_metrics(transfer_run_id),
    )


def _report_no_write_progress(
    progress_reporter: ProgressReporter | None,
    *,
    label: str,
) -> None:
    report_progress(
        progress_reporter,
        phase="write",
        current=0,
        total=0,
        label=label,
    )


def _finish_progress(progress_reporter: ProgressReporter | None) -> None:
    finish = getattr(progress_reporter, "finish", None)
    if callable(finish):
        finish()


def _resolve_destination_write_target(
    repository: TransferRepository,
    destination: BasePlatform,
    transfer_run_id: str,
    *,
    destination_playlist_id: str | None,
    create_playlist_name: str | None,
    default_create_name_suffix: str,
) -> str:
    run_record = repository.load_run(transfer_run_id)
    persisted_destination_id = _optional_text(run_record.destination_playlist_id)

    if destination_playlist_id is not None and create_playlist_name is not None:
        raise ValueError(
            "choose either destination_playlist_id or create_playlist, not both"
        )
    if (
        destination_playlist_id is not None
        and persisted_destination_id is not None
        and destination_playlist_id != persisted_destination_id
        and not destination.normalizes_destination_playlist_ids
    ):
        raise ValueError(
            "transfer run already targets destination playlist "
            f"{persisted_destination_id}; start a new match run to use "
            f"{destination_playlist_id}"
        )
    if destination_playlist_id is not None:
        normalized_destination_id = _normalize_destination_playlist_id(
            destination,
            destination_playlist_id,
        )
        normalized_persisted_destination_id = persisted_destination_id
        if (
            persisted_destination_id is not None
            and destination.normalizes_destination_playlist_ids
        ):
            normalized_persisted_destination_id = _normalize_destination_playlist_id(
                destination,
                persisted_destination_id,
            )
        if (
            normalized_persisted_destination_id is not None
            and normalized_destination_id != normalized_persisted_destination_id
        ):
            raise ValueError(
                "transfer run already targets destination playlist "
                f"{persisted_destination_id}; start a new match run to use "
                f"{normalized_destination_id}"
            )
        if normalized_destination_id != persisted_destination_id:
            repository.update_destination_playlist_id(
                transfer_run_id,
                normalized_destination_id,
            )
        logger.info("destination playlist id recorded", run_id=transfer_run_id)
        WRITE_DIAGNOSTICS.debug(
            "destination playlist id recorded",
            run_id=transfer_run_id,
            destination_platform=destination.platform_name,
            destination_playlist_id=normalized_destination_id,
        )
        return normalized_destination_id

    if persisted_destination_id is not None:
        normalized_destination_id = _normalize_destination_playlist_id(
            destination,
            persisted_destination_id,
        )
        if normalized_destination_id != persisted_destination_id:
            repository.update_destination_playlist_id(transfer_run_id, normalized_destination_id)
        WRITE_DIAGNOSTICS.debug(
            "destination playlist reused",
            run_id=transfer_run_id,
            destination_platform=destination.platform_name,
            destination_playlist_id=normalized_destination_id,
        )
        return normalized_destination_id

    if create_playlist_name is None:
        raise ValueError(
            "write target is required; pass --destination-playlist-id or --create-playlist"
        )

    destination_id = destination.create_playlist(
        create_playlist_name,
        f"Created by playlist-porter {default_create_name_suffix}",
    )
    repository.update_destination_playlist_id(transfer_run_id, destination_id)
    logger.info(
        "destination playlist created",
        run_id=transfer_run_id,
        destination_platform=destination.platform_name,
    )
    WRITE_DIAGNOSTICS.debug(
        "destination playlist created",
        run_id=transfer_run_id,
        destination_platform=destination.platform_name,
        destination_playlist_id=destination_id,
        create_playlist_name=create_playlist_name,
    )
    return destination_id


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _normalize_destination_playlist_id(destination: BasePlatform, playlist_id: str) -> str:
    return _optional_text(destination.validate_destination_playlist(playlist_id)) or playlist_id


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
) -> list[WritePair]:
    pairs: list[WritePair] = []
    for decision in decisions:
        source_track_id = str(decision.source_track.internal_id)
        override = overrides.get(source_track_id)
        candidate = _effective_selected_candidate(decision, override)
        WRITE_DIAGNOSTICS.debug(
            "write eligibility evaluated",
            source_track=track_summary(decision.source_track),
            decision_status=decision.status.value,
            override_status=override.status.value if override is not None else None,
            selected_candidate=candidate_summary(candidate),
            eligible=candidate is not None and candidate.track.platform_track_id is not None,
        )
        if candidate is None or candidate.track.platform_track_id is None:
            continue
        pairs.append(
            WritePair(
                source_track_id=source_track_id,
                track_id=candidate.track.platform_track_id,
                source_title=decision.source_track.title,
            )
        )
    WRITE_DIAGNOSTICS.debug(
        "eligible write pairs resolved",
        decision_count=len(decisions),
        override_count=len(overrides),
        eligible_write_count=len(pairs),
    )
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
    write_pairs: list[WritePair],
    repository: TransferRepository,
    transfer_run_id: str,
) -> list[WritePair]:
    pending_pairs = []
    for pair in write_pairs:
        should_write = repository.should_write_track(
            transfer_run_id,
            pair.source_track_id,
            pair.track_id,
        )
        WRITE_DIAGNOSTICS.debug(
            "write resume eligibility checked",
            run_id=transfer_run_id,
            pair=write_pair_snapshot(pair.source_track_id, pair.track_id),
            should_write=should_write,
        )
        if should_write:
            pending_pairs.append(pair)
        else:
            repository.record_write_skip(
                transfer_run_id,
                pair.source_track_id,
                pair.track_id,
                step_type=WRITE_SKIP_RESUME_STEP,
            )
    WRITE_DIAGNOSTICS.debug(
        "pending write pairs resolved",
        run_id=transfer_run_id,
        eligible_write_count=len(write_pairs),
        pending_write_count=len(pending_pairs),
    )
    return pending_pairs


def _filter_destination_duplicate_pairs(
    destination: BasePlatform,
    destination_playlist_id: str,
    pending_pairs: list[WritePair],
    *,
    repository: TransferRepository,
    transfer_run_id: str,
) -> list[WritePair]:
    existing_destination_ids = destination.get_destination_track_ids(destination_playlist_id)
    seen_destination_ids = set(existing_destination_ids)
    write_ready_pairs: list[WritePair] = []
    for pair in pending_pairs:
        if pair.track_id in seen_destination_ids:
            repository.record_write_skip(
                transfer_run_id,
                pair.source_track_id,
                pair.track_id,
                step_type=WRITE_SKIP_EXISTING_STEP,
            )
            WRITE_DIAGNOSTICS.debug(
                "destination duplicate write skipped",
                run_id=transfer_run_id,
                destination_platform=destination.platform_name,
                destination_playlist_id=destination_playlist_id,
                pair=write_pair_snapshot(pair.source_track_id, pair.track_id),
            )
            continue
        seen_destination_ids.add(pair.track_id)
        write_ready_pairs.append(pair)
    WRITE_DIAGNOSTICS.debug(
        "destination duplicate filter resolved",
        run_id=transfer_run_id,
        destination_platform=destination.platform_name,
        destination_playlist_id=destination_playlist_id,
        existing_destination_track_count=len(existing_destination_ids),
        pending_write_count=len(pending_pairs),
        write_ready_count=len(write_ready_pairs),
    )
    return write_ready_pairs


def _write_pending_pairs(
    destination: BasePlatform,
    destination_playlist_id: str,
    pending_pairs: list[WritePair],
    *,
    repository: TransferRepository,
    transfer_run_id: str,
    progress_reporter: ProgressReporter | None = None,
) -> int:
    if not pending_pairs:
        logger.info("no pending tracks to write", run_id=transfer_run_id)
        WRITE_DIAGNOSTICS.debug("no pending tracks to write", run_id=transfer_run_id)
        return 0

    report_progress(
        progress_reporter,
        phase="write",
        current=0,
        total=len(pending_pairs),
    )
    progress_writer = getattr(destination, "add_tracks_with_progress", None)
    if callable(progress_writer):
        try:
            WRITE_DIAGNOSTICS.debug(
                "progress writer started",
                run_id=transfer_run_id,
                destination_platform=destination.platform_name,
                destination_playlist_id=destination_playlist_id,
                pending_write_count=len(pending_pairs),
            )
            writer_kwargs = {
                "repository": repository,
                "transfer_run_id": transfer_run_id,
            }
            if _accepts_progress_reporter(progress_writer):
                writer_kwargs["progress_reporter"] = progress_reporter
            writer_args = [
                destination_playlist_id,
                [pair.source_track_id for pair in pending_pairs],
                [pair.track_id for pair in pending_pairs],
            ]
            if _accepts_source_titles(progress_writer):
                writer_args.append([pair.source_title for pair in pending_pairs])
            return int(
                progress_writer(
                    *writer_args,
                    **writer_kwargs,
                )
            )
        except Exception as exc:
            logger.error("progress write failed", run_id=transfer_run_id, error=exc)
            WRITE_DIAGNOSTICS.debug(
                "progress writer failed",
                run_id=transfer_run_id,
                destination_platform=destination.platform_name,
                destination_playlist_id=destination_playlist_id,
                error=exc,
            )
            _record_first_incomplete_write_failure(
                pending_pairs,
                repository=repository,
                transfer_run_id=transfer_run_id,
                error=str(exc) or exc.__class__.__name__,
            )
            raise

    written_count = 0
    for pair in pending_pairs:
        try:
            WRITE_DIAGNOSTICS.debug(
                "track write started",
                run_id=transfer_run_id,
                destination_platform=destination.platform_name,
                destination_playlist_id=destination_playlist_id,
                pair=write_pair_snapshot(pair.source_track_id, pair.track_id),
            )
            destination.add_tracks(destination_playlist_id, [pair.track_id])
        except Exception as exc:
            logger.error("track write failed", run_id=transfer_run_id, error=exc)
            WRITE_DIAGNOSTICS.debug(
                "track write failed",
                run_id=transfer_run_id,
                destination_platform=destination.platform_name,
                destination_playlist_id=destination_playlist_id,
                pair=write_pair_snapshot(pair.source_track_id, pair.track_id),
                error=exc,
            )
            repository.record_write_failure(
                transfer_run_id,
                pair.source_track_id,
                pair.track_id,
                error=str(exc) or exc.__class__.__name__,
            )
            raise
        repository.record_write_success(
            transfer_run_id,
            pair.source_track_id,
            pair.track_id,
        )
        written_count += 1
        report_progress(
            progress_reporter,
            phase="write",
            current=written_count,
            total=len(pending_pairs),
            label=pair.source_title,
        )
        logger.debug("track write recorded", run_id=transfer_run_id, written_count=written_count)
        WRITE_DIAGNOSTICS.debug(
            "track write recorded",
            run_id=transfer_run_id,
            pair=write_pair_snapshot(pair.source_track_id, pair.track_id),
            written_count=written_count,
        )
    return written_count


def _accepts_progress_reporter(progress_writer) -> bool:
    try:
        parameters = signature(progress_writer).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "progress_reporter"
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _accepts_source_titles(progress_writer) -> bool:
    try:
        parameters = signature(progress_writer).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "source_titles"
        or parameter.kind is Parameter.VAR_POSITIONAL
        for parameter in parameters
    )


def _record_first_incomplete_write_failure(
    pending_pairs: list[WritePair],
    *,
    repository: TransferRepository,
    transfer_run_id: str,
    error: str,
) -> None:
    for pair in pending_pairs:
        if repository.should_write_track(
            transfer_run_id,
            pair.source_track_id,
            pair.track_id,
        ):
            WRITE_DIAGNOSTICS.debug(
                "first incomplete write failure recorded",
                run_id=transfer_run_id,
                pair=write_pair_snapshot(pair.source_track_id, pair.track_id),
                error=error,
            )
            repository.record_write_failure(
                transfer_run_id,
                pair.source_track_id,
                pair.track_id,
                error=error,
            )
            return


def _credential_issues(
    adapter: BasePlatform,
    *,
    require_write: bool,
    require_playlist_read: bool = False,
) -> list[str]:
    if isinstance(adapter, SpotifyAdapter):
        if getattr(adapter, "_client", None) is not None:
            return []
        missing = adapter.config.missing_credentials()
        if not missing:
            return []
        if require_write:
            return [
                "Spotify OAuth credentials are required for write operations: "
                + ", ".join(f"SPOTIFY_{field.upper()}" for field in missing)
            ]
        if require_playlist_read:
            return [
                "Spotify OAuth credentials are required for playlist reads: "
                + ", ".join(f"SPOTIFY_{field.upper()}" for field in missing)
            ]
        return [
            "Spotify OAuth credentials are missing: "
            + ", ".join(f"SPOTIFY_{field.upper()}" for field in missing)
        ]
    if isinstance(adapter, QQMusicAdapter):
        if getattr(adapter, "_client", None) is not None:
            return []
        if not require_write and adapter.config.allow_anonymous_read:
            if adapter.config.credential_path is None:
                return []
            try:
                adapter.config.load_credential_payload()
            except FileNotFoundError as exc:
                return [f"QQ Music credential file is missing: {exc.filename}"]
            return []
        try:
            credential_payload = adapter.config.load_credential_payload()
        except FileNotFoundError as exc:
            return [f"QQ Music credential file is missing: {exc.filename}"]
        if credential_payload is None:
            return ["QQ Music credentials are missing: QQMUSIC_CREDENTIAL_PATH"]
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
    "ExecuteResult",
    "PlatformName",
    "PreflightError",
    "PreflightResult",
    "TransferResult",
    "create_mock_adapter",
    "create_platform_adapter",
    "execute_transfer_run",
    "execute_transfer_run_with_adapter",
    "execute_mock_transfer",
    "run_transfer",
    "run_transfer_with_adapters",
    "render_metrics",
    "validate_execute_preflight",
    "validate_transfer_preflight",
]
