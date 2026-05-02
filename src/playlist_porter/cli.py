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
from playlist_porter.workflow import dry_run_mock_transfer, execute_mock_transfer


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
