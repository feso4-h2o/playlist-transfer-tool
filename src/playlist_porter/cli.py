"""Command-line entry point for Playlist Porter."""

from __future__ import annotations

import argparse
import sys
from contextlib import AbstractContextManager, nullcontext
from types import TracebackType

from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Column

from playlist_porter import __version__
from playlist_porter.config import (
    PorterConfig,
    load_config,
    update_config_run_id,
    write_default_config,
)
from playlist_porter.config_validation import validate_write_target_config
from playlist_porter.logging_config import configure_logging
from playlist_porter.persistence.exports import export_reports
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.progress import ProgressEvent, ProgressReporter
from playlist_porter.rate_limit import (
    AuthenticationFailure,
    RateLimitExceeded,
    TemporaryServerError,
    TransientNetworkError,
    ValidationFailure,
)
from playlist_porter.review.terminal import (
    ReviewUpdate,
    apply_review_update,
    run_interactive_review,
)
from playlist_porter.workflow import (
    PreflightError,
    execute_transfer_run,
    run_transfer,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(prog="playlist-porter")
    parser.add_argument("--version", action="version", version=f"playlist-porter {__version__}")
    _add_logging_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-config", help="write a starter local config")
    _add_logging_arguments(init_parser)
    init_parser.add_argument("--path", default="cli-config.json")
    init_parser.add_argument("--force", action="store_true")

    match_parser = subparsers.add_parser(
        "match",
        help="read a source playlist and persist destination match candidates",
    )
    _add_logging_arguments(match_parser)
    _add_config_argument(match_parser)

    review_parser = subparsers.add_parser("review", help="review persisted uncertain matches")
    _add_logging_arguments(review_parser)
    _add_config_argument(review_parser)
    review_parser.add_argument("--source-track-id")
    review_parser.add_argument("--action", choices=["accept", "reject", "skip", "a", "r", "s"])
    review_parser.add_argument("--candidate-rank", type=int)

    write_parser = subparsers.add_parser("write", help="write approved matches from a reviewed run")
    _add_logging_arguments(write_parser)
    _add_config_argument(write_parser)

    export_parser = subparsers.add_parser("export-report", help="export transfer reports")
    _add_logging_arguments(export_parser)
    _add_config_argument(export_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""

    try:
        return _main(argv)
    except (
        AuthenticationFailure,
        PreflightError,
        RateLimitExceeded,
        TemporaryServerError,
        TransientNetworkError,
        ValidationFailure,
        ValueError,
    ) as exc:
        logger.error("command failed", error=exc)
        print(exc)
        return 1


def _main(argv: list[str] | None = None) -> int:
    """Run a parsed CLI command."""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging_setup = configure_logging(
        verbosity=getattr(args, "verbosity", 0),
        debug_log=getattr(args, "debug_log", False),
    )
    logger.info(
        "command started",
        command=args.command,
        verbosity=logging_setup.verbosity,
        debug_log=bool(logging_setup.log_path),
    )
    if args.command == "init-config":
        path = write_default_config(args.path, force=args.force)
        logger.info("config written", path=str(path), force=args.force)
        print(f"wrote config: {path}")
        return 0
    if args.command == "match":
        config = load_config(args.config)
        logger.info("config loaded", path=args.config)
        defaults = config.commands.match
        write_defaults = config.commands.write
        source_platform = _resolve_platform(
            config.source_platform,
            setting="source_platform",
        )
        destination_platform = _resolve_platform(
            config.destination_platform,
            setting="destination_platform",
        )
        source_playlist = _required(
            defaults.source_playlist,
            setting="match.source_playlist",
        )
        validate_write_target_config(
            destination_platform=destination_platform,
            destination_target_type=write_defaults.destination_target_type,
            destination_playlist_id=write_defaults.destination_playlist_id,
            create_playlist=write_defaults.create_playlist,
        )
        logger.info(
            "match command resolved",
            source_platform=source_platform,
            destination_platform=destination_platform,
            database_path=str(config.database_path),
            output_dir=str(config.report_output_dir),
            output_format=config.report_format,
            restart=bool(_coalesce(defaults.restart, False)),
        )
        with _progress_context(logging_setup) as progress_reporter:
            result = run_transfer(
                config,
                source_platform=source_platform,
                destination_platform=destination_platform,
                source_playlist_id=source_playlist,
                dry_run=True,
                restart=_coalesce(defaults.restart, False),
                destination_playlist_id=write_defaults.destination_playlist_id,
                create_playlist_name=write_defaults.create_playlist,
                destination_target_type=write_defaults.destination_target_type,
                progress_reporter=progress_reporter,
            )
        update_config_run_id(args.config, result.transfer_run_id)
        logger.info(
            "match command finished",
            run_id=result.transfer_run_id,
            written=result.written_count,
            skipped=result.skipped_count,
            reports=[str(path) for path in result.report_paths],
        )
        print(f"run id: {result.transfer_run_id}")
        print("mode: match")
        print(f"written: {result.written_count}; skipped: {result.skipped_count}")
        for path in result.report_paths:
            print(f"wrote report: {path}")
        return 0
    if args.command == "review":
        config = load_config(args.config)
        logger.info("config loaded", path=args.config)
        run_id = _required(config.run_id, setting="run_id")
        defaults = config.commands.review
        candidate_rank = _coalesce(args.candidate_rank, 1)
        repository = TransferRepository(config.database_path)
        _validate_run_direction(repository, run_id, config)
        if args.action:
            if not args.source_track_id:
                raise SystemExit("--source-track-id is required with --action")
            apply_review_update(
                repository,
                run_id,
                ReviewUpdate(
                    source_track_internal_id=args.source_track_id,
                    action=args.action,
                    candidate_rank=candidate_rank,
                ),
            )
            logger.info(
                "review update saved",
                run_id=run_id,
                action=args.action,
                candidate_rank=candidate_rank,
            )
            print("saved review update")
        else:
            saved_count = run_interactive_review(
                repository,
                run_id,
                pending_only=bool(defaults.pending_only),
                show_position=_should_show_progress(logging_setup),
            )
            logger.info("interactive review finished", run_id=run_id, saved_count=saved_count)
            print(f"saved review updates: {saved_count}")
        return 0
    if args.command == "write":
        config = load_config(args.config)
        logger.info("config loaded", path=args.config)
        defaults = config.commands.write
        run_id = _required(config.run_id, setting="run_id")
        repository = TransferRepository(config.database_path)
        run_record = _validate_run_direction(repository, run_id, config)
        destination_platform = _resolve_platform(
            run_record.destination_platform,
            setting="persisted run destination_platform",
        )
        validate_write_target_config(
            destination_platform=destination_platform,
            destination_target_type=defaults.destination_target_type,
            destination_playlist_id=defaults.destination_playlist_id,
            create_playlist=defaults.create_playlist,
        )
        logger.info(
            "write command resolved",
            destination_platform=destination_platform,
            run_id=run_id,
            database_path=str(config.database_path),
            output_dir=str(config.report_output_dir),
            output_format=config.report_format,
        )
        with _progress_context(logging_setup) as progress_reporter:
            result = execute_transfer_run(
                config,
                destination_platform=destination_platform,
                transfer_run_id=run_id,
                destination_playlist_id=defaults.destination_playlist_id,
                create_playlist_name=defaults.create_playlist,
                destination_target_type=defaults.destination_target_type,
                progress_reporter=progress_reporter,
            )
        logger.info(
            "write command finished",
            run_id=result.transfer_run_id,
            written=result.written_count,
            skipped=result.skipped_count,
            reports=[str(path) for path in result.report_paths],
        )
        print(f"run id: {result.transfer_run_id}")
        print("mode: write")
        print(f"destination target: {result.destination_playlist_id}")
        print(f"written: {result.written_count}; skipped: {result.skipped_count}")
        for path in result.report_paths:
            print(f"wrote report: {path}")
        return 0
    if args.command == "export-report":
        config = load_config(args.config)
        logger.info("config loaded", path=args.config)
        run_id = _required(config.run_id, setting="run_id")
        repository = TransferRepository(config.database_path)
        _validate_run_direction(repository, run_id, config)
        paths = export_reports(
            repository,
            run_id,
            config.report_output_dir,
            output_format=config.report_format,
            command="export-report",
        )
        logger.info(
            "reports exported",
            run_id=run_id,
            output_dir=str(config.report_output_dir),
            output_format=config.report_format,
            reports=[str(path) for path in paths],
        )
        for path in paths:
            print(f"wrote report: {path}")
        return 0
    raise SystemExit(f"unknown command: {args.command}")


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True)


def _add_logging_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
        dest="verbosity",
        help="show INFO logs; repeat for DEBUG logs",
    )
    parser.add_argument(
        "-l",
        "--log",
        action="store_true",
        default=argparse.SUPPRESS,
        dest="debug_log",
        help="write DEBUG logs to logs/",
    )


class _RichProgressReporter:
    def __init__(self) -> None:
        self._progress = Progress(
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            BarColumn(),
            TextColumn(
                "{task.description}",
                table_column=Column(ratio=1, overflow="ellipsis"),
            ),
            console=Console(stderr=True),
            expand=True,
            transient=False,
        )
        self._task_ids: dict[str, int] = {}
        self._last_events: dict[str, ProgressEvent] = {}
        self._finished = False

    def __enter__(self) -> ProgressReporter:
        self._progress.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        success = exc_type is None
        del exc, traceback
        self.finish(success=success)

    def finish(self, *, success: bool = True) -> None:
        if self._finished:
            return
        if success:
            self._finish_tasks()
        self._progress.stop()
        self._progress.console.print()
        self._finished = True

    def __call__(self, event: ProgressEvent) -> None:
        self._last_events[event.phase] = event
        task_id = self._task_ids.get(event.phase)
        description = _progress_description(event)
        if task_id is None:
            task_id = self._progress.add_task(
                description,
                total=max(event.total, 0),
                completed=max(event.current, 0),
            )
            self._task_ids[event.phase] = task_id
            return
        self._progress.update(
            task_id,
            total=max(event.total, 0),
            completed=max(event.current, 0),
            description=description,
        )

    def _finish_tasks(self) -> None:
        for phase, task_id in self._task_ids.items():
            event = self._last_events[phase]
            total = max(event.total, 0)
            self._progress.update(
                task_id,
                total=total,
                completed=total,
                description=_finished_progress_description(event),
            )


def _progress_context(logging_setup) -> AbstractContextManager[ProgressReporter | None]:
    if not _should_show_progress(logging_setup):
        return nullcontext(None)
    return _RichProgressReporter()


def _should_show_progress(logging_setup) -> bool:
    return (
        logging_setup.verbosity == 0
        and logging_setup.log_path is None
        and sys.stdout.isatty()
        and sys.stderr.isatty()
    )


def _progress_description(event: ProgressEvent) -> str:
    label = event.label.strip() if event.label else ""
    if label == "Checking destination for existing tracks...":
        return label
    if event.phase == "match":
        prefix = "Matching tracks"
    else:
        prefix = "Writing tracks"
    return f"{prefix}: {label}" if label else prefix


def _finished_progress_description(event: ProgressEvent) -> str:
    label = event.label.strip() if event.label else ""
    if event.total == 0 and label:
        return f"Done! {label}"
    return "Done!"


def _coalesce(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _required(value, *, setting: str):
    if value is None:
        raise SystemExit(f"{setting} is required; set it in config")
    return value


def _resolve_platform(value: str | None, *, setting: str):
    platform = _required(value, setting=setting)
    if platform not in {"mock", "spotify", "qqmusic"}:
        raise SystemExit(f"{setting} must be one of mock, spotify, qqmusic")
    return platform


def _validate_run_direction(
    repository: TransferRepository,
    run_id: str,
    config: PorterConfig,
):
    run_record = repository.load_run(run_id)
    if config.source_platform and run_record.source_platform != config.source_platform:
        raise ValueError(
            "configured source_platform "
            f"{config.source_platform} does not match persisted run source "
            f"{run_record.source_platform}"
        )
    if (
        config.destination_platform
        and run_record.destination_platform != config.destination_platform
    ):
        raise ValueError(
            "configured destination_platform "
            f"{config.destination_platform} does not match persisted run destination "
            f"{run_record.destination_platform}"
        )
    return run_record


__all__ = ["build_parser", "main"]
