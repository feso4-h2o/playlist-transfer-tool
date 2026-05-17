"""Reset local write resume markers for one transfer run.

This script removes only ``write_track`` rows from ``transfer_steps`` for the
selected run. Match decisions, review overrides, source tracks, candidates, and
the destination playlist target are left intact.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DATABASE = Path("state/playlist-porter.sqlite")
DEFAULT_STEP_TYPE = "write_track"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    database_path = Path(args.database)
    if not database_path.exists():
        parser.error(f"database does not exist: {database_path}")

    try:
        summary = _load_summary(database_path, run_id=args.run_id, step_type=args.step_type)
    except sqlite3.Error as exc:
        print(f"failed to inspect database: {exc}", file=sys.stderr)
        return 1

    if summary is None:
        print(f"transfer run not found: {args.run_id}", file=sys.stderr)
        return 2

    _print_summary(summary, dry_run=not args.yes)
    if not args.yes:
        print("dry run only; pass --yes to delete these write markers")
        return 0

    if not args.no_backup:
        backup_path = _backup_path(database_path)
        shutil.copy2(database_path, backup_path)
        print(f"backup written: {backup_path}")

    try:
        deleted_count = _reset_write_status(
            database_path,
            run_id=args.run_id,
            step_type=args.step_type,
        )
    except sqlite3.Error as exc:
        print(f"failed to reset write status: {exc}", file=sys.stderr)
        return 1

    print(f"deleted write markers: {deleted_count}")
    print("cleared transfer_metrics row so metrics will recompute on next load")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset local write status for one playlist-porter transfer run.",
    )
    parser.add_argument("--run-id", required=True, help="Transfer run UUID/internal_id to reset.")
    parser.add_argument(
        "--database",
        "-d",
        default=str(DEFAULT_DATABASE),
        help=f"SQLite database path. Defaults to {DEFAULT_DATABASE}.",
    )
    parser.add_argument(
        "--step-type",
        default=DEFAULT_STEP_TYPE,
        help=f"Transfer step type to remove. Defaults to {DEFAULT_STEP_TYPE}.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete write markers. Without this flag, only prints a dry-run summary.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating a timestamped .bak copy before deleting rows.",
    )
    return parser


def _load_summary(
    database_path: Path,
    *,
    run_id: str,
    step_type: str,
) -> dict[str, object] | None:
    with sqlite3.connect(database_path) as connection:
        run_row = connection.execute(
            """
            select internal_id, source_platform, destination_platform, destination_playlist_id
            from transfer_runs
            where internal_id = ?
            """,
            (run_id,),
        ).fetchone()
        if run_row is None:
            return None

        step_rows = connection.execute(
            """
            select status, count(*)
            from transfer_steps
            where transfer_run_id = ? and step_type = ?
            group by status
            order by status
            """,
            (run_id, step_type),
        ).fetchall()

    return {
        "run_id": run_row[0],
        "source_platform": run_row[1],
        "destination_platform": run_row[2],
        "destination_playlist_id": run_row[3],
        "step_type": step_type,
        "status_counts": dict(step_rows),
        "total_steps": sum(count for _, count in step_rows),
    }


def _print_summary(summary: dict[str, object], *, dry_run: bool) -> None:
    status_counts = summary["status_counts"]
    assert isinstance(status_counts, dict)
    counts = ", ".join(f"{status}={count}" for status, count in status_counts.items()) or "none"
    mode = "dry run" if dry_run else "reset"
    print(f"mode: {mode}")
    print(f"run id: {summary['run_id']}")
    print(
        "source -> destination: "
        f"{summary['source_platform']} -> {summary['destination_platform']}"
    )
    print(f"destination playlist: {summary['destination_playlist_id'] or '(none)'}")
    print(f"step type: {summary['step_type']}")
    print(f"write markers found: {summary['total_steps']} ({counts})")


def _reset_write_status(database_path: Path, *, run_id: str, step_type: str) -> int:
    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute(
            """
            delete from transfer_steps
            where transfer_run_id = ? and step_type = ?
            """,
            (run_id, step_type),
        )
        deleted_count = cursor.rowcount
        connection.execute(
            "delete from transfer_metrics where transfer_run_id = ?",
            (run_id,),
        )
        connection.commit()
    return deleted_count


def _backup_path(database_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return database_path.with_name(f"{database_path.name}.{timestamp}.bak")


if __name__ == "__main__":
    raise SystemExit(main())
