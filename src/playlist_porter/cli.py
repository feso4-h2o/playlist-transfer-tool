"""Command-line entry point for Playlist Porter."""

from __future__ import annotations

import argparse
from pathlib import Path

from playlist_porter import __version__
from playlist_porter.config import PorterConfig, load_config, write_default_config
from playlist_porter.persistence.exports import export_reports
from playlist_porter.persistence.repositories import TransferRepository
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
    dry_run_mock_transfer,
    execute_transfer_run,
    run_transfer,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(prog="playlist-porter")
    parser.add_argument("--version", action="version", version=f"playlist-porter {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-config", help="write a starter local config")
    init_parser.add_argument("--path", default="playlist-porter.json")
    init_parser.add_argument("--force", action="store_true")

    dry_run_parser = subparsers.add_parser("dry-run", help="run mock matching without writes")
    _add_config_argument(dry_run_parser)
    dry_run_parser.add_argument("--source-playlist", required=True)
    dry_run_parser.add_argument("--db")
    dry_run_parser.add_argument("--restart", action="store_true")

    match_parser = subparsers.add_parser(
        "match",
        help="read a source playlist and persist destination match candidates",
    )
    _add_config_argument(match_parser)
    match_parser.add_argument(
        "--source-platform",
        choices=["mock", "spotify", "qqmusic"],
    )
    match_parser.add_argument(
        "--destination-platform",
        choices=["mock", "spotify", "qqmusic"],
    )
    match_parser.add_argument("--source-playlist")
    match_parser.add_argument("--db")
    match_parser.add_argument("--output-dir")
    restart_mode = match_parser.add_mutually_exclusive_group()
    restart_mode.add_argument("--restart", dest="restart", action="store_true", default=None)
    restart_mode.add_argument("--no-restart", dest="restart", action="store_false")

    review_parser = subparsers.add_parser("review", help="review persisted uncertain matches")
    review_parser.add_argument("--config")
    review_parser.add_argument("--db")
    review_parser.add_argument("--run-id")
    review_parser.add_argument("--source-track-id")
    review_parser.add_argument("--action", choices=["accept", "reject", "skip"])
    review_parser.add_argument("--candidate-rank", type=int)

    write_parser = subparsers.add_parser("write", help="write approved matches from a reviewed run")
    _add_config_argument(write_parser)
    write_parser.add_argument(
        "--destination-platform",
        choices=["mock", "spotify", "qqmusic"],
    )
    write_parser.add_argument("--run-id")
    write_parser.add_argument("--db")
    write_parser.add_argument("--output-dir")
    write_parser.add_argument("--destination-playlist-id")
    write_parser.add_argument("--create-playlist")

    export_parser = subparsers.add_parser("export-report", help="export transfer reports")
    export_parser.add_argument("--config")
    export_parser.add_argument("--db")
    export_parser.add_argument("--run-id")
    export_parser.add_argument("--output-dir")
    export_parser.add_argument("--format", choices=["csv", "json", "both"])
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
        print(exc)
        return 1


def _main(argv: list[str] | None = None) -> int:
    """Run a parsed CLI command."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-config":
        path = write_default_config(args.path, force=args.force)
        print(f"wrote config: {path}")
        return 0
    if args.command == "dry-run":
        config = load_config(args.config)
        result = dry_run_mock_transfer(
            config,
            source_playlist_id=args.source_playlist,
            database_path=args.db,
            restart=args.restart,
        )
        print(f"run id: {result.transfer_run_id}")
        return 0
    if args.command == "match":
        config = load_config(args.config)
        defaults = config.commands.match
        source_platform = _resolve_platform(
            _coalesce(args.source_platform, defaults.source_platform),
            setting="match.source_platform",
            flag="--source-platform",
        )
        destination_platform = _resolve_platform(
            _coalesce(args.destination_platform, defaults.destination_platform),
            setting="match.destination_platform",
            flag="--destination-platform",
        )
        database_path = _coalesce(args.db, defaults.database_path)
        output_dir = _coalesce(args.output_dir, defaults.output_dir)
        source_playlist = _required(
            _coalesce(args.source_playlist, defaults.source_playlist),
            setting="match.source_playlist",
            flag="--source-playlist",
        )
        result = run_transfer(
            config,
            source_platform=source_platform,
            destination_platform=destination_platform,
            source_playlist_id=source_playlist,
            dry_run=True,
            database_path=database_path,
            output_dir=output_dir,
            restart=_coalesce(args.restart, defaults.restart, False),
        )
        print(f"run id: {result.transfer_run_id}")
        print("mode: match")
        print(f"written: {result.written_count}; skipped: {result.skipped_count}")
        for path in result.report_paths:
            print(f"wrote report: {path}")
        return 0
    if args.command == "review":
        config = _load_optional_config(args.config)
        defaults = config.commands.review if config is not None else None
        database_path = _required(
            _coalesce(
                args.db,
                getattr(defaults, "database_path", None),
                config.database_path if config is not None else None,
            ),
            setting="review.database_path",
            flag="--db",
        )
        run_id = _required(
            _coalesce(args.run_id, getattr(defaults, "run_id", None)),
            setting="review.run_id",
            flag="--run-id",
        )
        candidate_rank = _coalesce(
            args.candidate_rank,
            getattr(defaults, "candidate_rank", None),
            1,
        )
        repository = TransferRepository(database_path)
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
            print("saved review update")
        else:
            saved_count = run_interactive_review(repository, run_id)
            print(f"saved review updates: {saved_count}")
        return 0
    if args.command == "write":
        config = load_config(args.config)
        defaults = config.commands.write
        destination_platform = _resolve_platform(
            _coalesce(args.destination_platform, defaults.destination_platform),
            setting="write.destination_platform",
            flag="--destination-platform",
        )
        run_id = _required(
            _coalesce(args.run_id, defaults.run_id),
            setting="write.run_id",
            flag="--run-id",
        )
        result = execute_transfer_run(
            config,
            destination_platform=destination_platform,
            transfer_run_id=run_id,
            database_path=_coalesce(args.db, defaults.database_path),
            output_dir=_coalesce(args.output_dir, defaults.output_dir),
            destination_playlist_id=_coalesce(
                args.destination_playlist_id,
                defaults.destination_playlist_id,
            ),
            create_playlist_name=_coalesce(args.create_playlist, defaults.create_playlist),
        )
        print(f"run id: {result.transfer_run_id}")
        print("mode: write")
        print(f"destination playlist: {result.destination_playlist_id}")
        print(f"written: {result.written_count}; skipped: {result.skipped_count}")
        for path in result.report_paths:
            print(f"wrote report: {path}")
        return 0
    if args.command == "export-report":
        config = _load_optional_config(args.config)
        defaults = config.commands.export_report if config is not None else None
        database_path = _required(
            _coalesce(
                args.db,
                getattr(defaults, "database_path", None),
                config.database_path if config is not None else None,
            ),
            setting="export_report.database_path",
            flag="--db",
        )
        run_id = _required(
            _coalesce(args.run_id, getattr(defaults, "run_id", None)),
            setting="export_report.run_id",
            flag="--run-id",
        )
        output_dir = _required(
            _coalesce(
                args.output_dir,
                getattr(defaults, "output_dir", None),
                config.report_output_dir if config is not None else None,
            ),
            setting="export_report.output_dir",
            flag="--output-dir",
        )
        output_format = _coalesce(args.format, getattr(defaults, "output_format", None), "both")
        if output_format not in {"csv", "json", "both"}:
            raise SystemExit("export_report.format must be one of csv, json, both")
        paths = export_reports(
            TransferRepository(database_path),
            run_id,
            Path(output_dir),
            output_format=output_format,
        )
        for path in paths:
            print(f"wrote report: {path}")
        return 0
    raise SystemExit(f"unknown command: {args.command}")


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True)


def _load_optional_config(path: str | None) -> PorterConfig | None:
    if path is None:
        return None
    return load_config(path)


def _coalesce(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _required(value, *, setting: str, flag: str):
    if value is None:
        raise SystemExit(f"{setting} is required; set it in config or pass {flag}")
    return value


def _resolve_platform(value: str | None, *, setting: str, flag: str):
    platform = _required(value, setting=setting, flag=flag)
    if platform not in {"mock", "spotify", "qqmusic"}:
        raise SystemExit(f"{setting} must be one of mock, spotify, qqmusic")
    return platform


__all__ = ["build_parser", "main"]
