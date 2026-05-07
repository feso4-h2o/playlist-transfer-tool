"""Command-line entry point for Playlist Porter."""

from __future__ import annotations

import argparse
from pathlib import Path

from playlist_porter import __version__
from playlist_porter.config import load_config, write_default_config
from playlist_porter.persistence.exports import export_reports
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.review.terminal import (
    ReviewUpdate,
    apply_review_update,
    run_interactive_review,
)
from playlist_porter.workflow import (
    dry_run_mock_transfer,
    execute_mock_transfer,
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

    transfer_parser = subparsers.add_parser(
        "transfer",
        help="run a direction-aware transfer dry-run or write execution",
    )
    _add_config_argument(transfer_parser)
    transfer_parser.add_argument(
        "--source-platform",
        choices=["mock", "spotify", "qqmusic"],
    )
    transfer_parser.add_argument(
        "--destination-platform",
        choices=["mock", "spotify", "qqmusic"],
        required=True,
    )
    transfer_parser.add_argument("--source-playlist")
    transfer_parser.add_argument("--run-id")
    transfer_parser.add_argument("--db")
    transfer_parser.add_argument("--output-dir")
    transfer_parser.add_argument("--restart", action="store_true")
    transfer_parser.add_argument("--destination-playlist-id")
    transfer_parser.add_argument("--create-playlist")
    mode = transfer_parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--write", dest="dry_run", action="store_false")

    review_parser = subparsers.add_parser("review", help="review persisted uncertain matches")
    review_parser.add_argument("--db", required=True)
    review_parser.add_argument("--run-id", required=True)
    review_parser.add_argument("--source-track-id")
    review_parser.add_argument("--action", choices=["accept", "reject", "skip"])
    review_parser.add_argument("--candidate-rank", type=int, default=1)

    execute_parser = subparsers.add_parser("execute", help="write approved mock tracks")
    _add_config_argument(execute_parser)
    execute_parser.add_argument("--run-id", required=True)
    execute_parser.add_argument("--db")
    execute_parser.add_argument("--destination-playlist-id")
    execute_parser.add_argument("--create-playlist")

    resume_parser = subparsers.add_parser("resume", help="continue approved mock writes")
    _add_config_argument(resume_parser)
    resume_parser.add_argument("--run-id", required=True)
    resume_parser.add_argument("--db")

    export_parser = subparsers.add_parser("export-report", help="export transfer reports")
    export_parser.add_argument("--db", required=True)
    export_parser.add_argument("--run-id", required=True)
    export_parser.add_argument("--output-dir", required=True)
    export_parser.add_argument("--format", choices=["csv", "json", "both"], default="both")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""

    args = build_parser().parse_args(argv)
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
    if args.command == "transfer":
        config = load_config(args.config)
        if args.run_id:
            if args.dry_run:
                raise SystemExit("--run-id can only be used with --write")
            result = execute_transfer_run(
                config,
                destination_platform=args.destination_platform,
                transfer_run_id=args.run_id,
                database_path=args.db,
                output_dir=args.output_dir,
                destination_playlist_id=args.destination_playlist_id,
                create_playlist_name=args.create_playlist,
            )
        else:
            if not args.source_platform:
                raise SystemExit("--source-platform is required without --run-id")
            if not args.source_playlist:
                raise SystemExit("--source-playlist is required without --run-id")
            result = run_transfer(
                config,
                source_platform=args.source_platform,
                destination_platform=args.destination_platform,
                source_playlist_id=args.source_playlist,
                dry_run=args.dry_run,
                database_path=args.db,
                output_dir=args.output_dir,
                restart=args.restart,
                destination_playlist_id=args.destination_playlist_id,
                create_playlist_name=args.create_playlist,
            )
        print(f"run id: {result.transfer_run_id}")
        print(f"mode: {'dry-run' if result.dry_run else 'write'}")
        if result.destination_playlist_id:
            print(f"destination playlist: {result.destination_playlist_id}")
        print(f"written: {result.written_count}; skipped: {result.skipped_count}")
        for path in result.report_paths:
            print(f"wrote report: {path}")
        return 0
    if args.command == "review":
        repository = TransferRepository(args.db)
        if args.action:
            if not args.source_track_id:
                raise SystemExit("--source-track-id is required with --action")
            apply_review_update(
                repository,
                args.run_id,
                ReviewUpdate(
                    source_track_internal_id=args.source_track_id,
                    action=args.action,
                    candidate_rank=args.candidate_rank,
                ),
            )
            print("saved review update")
        else:
            saved_count = run_interactive_review(repository, args.run_id)
            print(f"saved review updates: {saved_count}")
        return 0
    if args.command == "execute":
        config = load_config(args.config)
        result = execute_mock_transfer(
            config,
            transfer_run_id=args.run_id,
            database_path=args.db,
            destination_playlist_id=args.destination_playlist_id,
            create_playlist_name=args.create_playlist,
        )
        print(f"destination playlist: {result.destination_playlist_id}")
        print(f"written: {result.attempted_count}; skipped: {result.skipped_count}")
        return 0
    if args.command == "resume":
        config = load_config(args.config)
        result = execute_mock_transfer(
            config,
            transfer_run_id=args.run_id,
            database_path=args.db,
        )
        print(f"destination playlist: {result.destination_playlist_id}")
        print(f"written: {result.attempted_count}; skipped: {result.skipped_count}")
        return 0
    if args.command == "export-report":
        paths = export_reports(
            TransferRepository(args.db),
            args.run_id,
            Path(args.output_dir),
            output_format=args.format,
        )
        for path in paths:
            print(f"wrote report: {path}")
        return 0
    raise SystemExit(f"unknown command: {args.command}")


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True)


__all__ = ["build_parser", "main"]
