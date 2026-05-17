"""Reset local write resume markers for one transfer run.

By default this removes all local write-status rows from ``transfer_steps`` for
the selected run, including successful writes and destination/resume skips.
Match decisions, review overrides, source tracks, candidates, and the
destination playlist target are left intact.
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
DEFAULT_STEP_TYPES = ("write_track", "write_skip_existing", "write_skip_resume")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    database_path = Path(args.database)
    if not database_path.exists():
        parser.error(f"database does not exist: {database_path}")

    try:
        step_types = tuple(args.step_type) if args.step_type else DEFAULT_STEP_TYPES
        summary = _load_summary(database_path, run_id=args.run_id, step_types=step_types)
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
            step_types=step_types,
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
        action="append",
        choices=DEFAULT_STEP_TYPES,
        help=(
            "Transfer step type to remove. May be passed more than once. "
            f"Defaults to all write status step types: {', '.join(DEFAULT_STEP_TYPES)}."
        ),
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
    step_types: Sequence[str],
) -> dict[str, object] | None:
    placeholders = _step_type_placeholders(step_types)
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
            f"""
            select step_type, status, count(*)
            from transfer_steps
            where transfer_run_id = ? and step_type in ({placeholders})
            group by step_type, status
            order by step_type, status
            """,
            (run_id, *step_types),
        ).fetchall()

    return {
        "run_id": run_row[0],
        "source_platform": run_row[1],
        "destination_platform": run_row[2],
        "destination_playlist_id": run_row[3],
        "step_types": step_types,
        "status_counts": {(step_type, status): count for step_type, status, count in step_rows},
        "total_steps": sum(count for _, _, count in step_rows),
    }


def _print_summary(summary: dict[str, object], *, dry_run: bool) -> None:
    status_counts = summary["status_counts"]
    assert isinstance(status_counts, dict)
    counts = (
        ", ".join(
            f"{step_type}:{status}={count}"
            for (step_type, status), count in status_counts.items()
        )
        or "none"
    )
    mode = "dry run" if dry_run else "reset"
    print(f"mode: {mode}")
    print(f"run id: {summary['run_id']}")
    print(
        "source -> destination: "
        f"{summary['source_platform']} -> {summary['destination_platform']}"
    )
    print(f"destination playlist: {summary['destination_playlist_id'] or '(none)'}")
    step_types = summary["step_types"]
    assert isinstance(step_types, Sequence)
    print(f"step types: {', '.join(step_types)}")
    print(f"write markers found: {summary['total_steps']} ({counts})")


def _reset_write_status(database_path: Path, *, run_id: str, step_types: Sequence[str]) -> int:
    placeholders = _step_type_placeholders(step_types)
    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute(
            f"""
            delete from transfer_steps
            where transfer_run_id = ? and step_type in ({placeholders})
            """,
            (run_id, *step_types),
        )
        deleted_count = cursor.rowcount
        connection.execute(
            "delete from transfer_metrics where transfer_run_id = ?",
            (run_id,),
        )
        connection.commit()
    return deleted_count


def _step_type_placeholders(step_types: Sequence[str]) -> str:
    if not step_types:
        raise ValueError("at least one step type is required")
    return ", ".join("?" for _ in step_types)


def _backup_path(database_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return database_path.with_name(f"{database_path.name}.{timestamp}.bak")


if __name__ == "__main__":
    raise SystemExit(main())
