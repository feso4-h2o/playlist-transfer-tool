"""SQLite schema and engine helpers for transfer state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine

metadata = MetaData()


transfer_runs = Table(
    "transfer_runs",
    metadata,
    Column("internal_id", String(36), primary_key=True),
    Column("run_key", String(255), unique=True),
    Column("source_platform", String(64), nullable=False),
    Column("destination_platform", String(64), nullable=False),
    Column("source_playlist_id", String(255)),
    Column("source_playlist_name", String(255)),
    Column("destination_playlist_id", String(255)),
    Column("dry_run", Boolean, nullable=False, default=True),
    Column("started_at", String(40), nullable=False),
    Column("completed_at", String(40)),
    Column("metadata_json", Text, nullable=False, default="{}"),
)

source_tracks = Table(
    "source_tracks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "transfer_run_id",
        String(36),
        ForeignKey("transfer_runs.internal_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("internal_id", String(36), nullable=False),
    Column("position", Integer),
    Column("platform", String(64)),
    Column("platform_track_id", String(255)),
    Column("title", String(500), nullable=False),
    Column("artists_json", Text, nullable=False),
    Column("album", String(500)),
    Column("isrc", String(64)),
    Column("duration_seconds", Integer),
    Column("release_date", String(20)),
    Column("release_year", Integer),
    Column("explicit", Boolean),
    Column("track_fingerprint", String(255), nullable=False),
    UniqueConstraint("transfer_run_id", "internal_id", name="uq_source_tracks_run_internal"),
)

candidate_tracks = Table(
    "candidate_tracks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "transfer_run_id",
        String(36),
        ForeignKey("transfer_runs.internal_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_track_internal_id", String(36), nullable=False),
    Column("candidate_internal_id", String(36), nullable=False),
    Column("rank", Integer, nullable=False),
    Column("query", Text),
    Column("score", Float, nullable=False),
    Column("unavailable_reason", String(64)),
    Column("evidence_json", Text, nullable=False, default="{}"),
    Column("platform", String(64)),
    Column("platform_track_id", String(255)),
    Column("title", String(500), nullable=False),
    Column("artists_json", Text, nullable=False),
    Column("album", String(500)),
    Column("isrc", String(64)),
    Column("duration_seconds", Integer),
    Column("release_date", String(20)),
    Column("release_year", Integer),
    Column("explicit", Boolean),
    Column("track_fingerprint", String(255), nullable=False),
    UniqueConstraint(
        "transfer_run_id",
        "source_track_internal_id",
        "candidate_internal_id",
        name="uq_candidate_tracks_run_source_candidate",
    ),
)

match_decisions = Table(
    "match_decisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "transfer_run_id",
        String(36),
        ForeignKey("transfer_runs.internal_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_track_internal_id", String(36), nullable=False),
    Column("status", String(64), nullable=False),
    Column("selected_candidate_internal_id", String(36)),
    Column("score", Float),
    Column("evidence_json", Text, nullable=False, default="{}"),
    Column("reason_codes_json", Text, nullable=False, default="[]"),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint(
        "transfer_run_id",
        "source_track_internal_id",
        name="uq_match_decisions_run_source",
    ),
)

user_overrides = Table(
    "user_overrides",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "transfer_run_id",
        String(36),
        ForeignKey("transfer_runs.internal_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_track_internal_id", String(36), nullable=False),
    Column("status", String(64), nullable=False),
    Column("selected_candidate_internal_id", String(36)),
    Column("reason_codes_json", Text, nullable=False, default="[]"),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint(
        "transfer_run_id",
        "source_track_internal_id",
        name="uq_user_overrides_run_source",
    ),
)

transfer_steps = Table(
    "transfer_steps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "transfer_run_id",
        String(36),
        ForeignKey("transfer_runs.internal_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("step_type", String(64), nullable=False),
    Column("source_track_internal_id", String(36)),
    Column("destination_track_id", String(255)),
    Column("status", String(64), nullable=False),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("error", Text),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("completed_at", String(40)),
    UniqueConstraint(
        "transfer_run_id",
        "step_type",
        "source_track_internal_id",
        "destination_track_id",
        name="uq_transfer_steps_run_step_source_destination",
    ),
)

transfer_metrics = Table(
    "transfer_metrics",
    metadata,
    Column(
        "transfer_run_id",
        String(36),
        ForeignKey("transfer_runs.internal_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("source_track_count", Integer, nullable=False, default=0),
    Column("candidate_count", Integer, nullable=False, default=0),
    Column("auto_accepted_count", Integer, nullable=False, default=0),
    Column("review_required_count", Integer, nullable=False, default=0),
    Column("not_found_count", Integer, nullable=False, default=0),
    Column("user_approved_count", Integer, nullable=False, default=0),
    Column("user_rejected_count", Integer, nullable=False, default=0),
    Column("write_success_count", Integer, nullable=False, default=0),
    Column("write_failure_count", Integer, nullable=False, default=0),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("elapsed_runtime_seconds", Float, nullable=False, default=0.0),
    Column("status_counts_json", Text, nullable=False, default="{}"),
    Column("unavailable_reason_counts_json", Text, nullable=False, default="{}"),
    Column("updated_at", String(40), nullable=False),
)


def create_sqlite_engine(database_path: str | Path = ":memory:", **kwargs: Any) -> Engine:
    """Create a SQLite engine with foreign-key enforcement enabled."""

    if str(database_path) == ":memory:":
        url = "sqlite+pysqlite:///:memory:"
    else:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+pysqlite:///{path.as_posix()}"

    engine = create_engine(url, future=True, **kwargs)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    """Create all persistence tables on the provided engine."""

    metadata.create_all(engine)


__all__ = [
    "candidate_tracks",
    "create_schema",
    "create_sqlite_engine",
    "match_decisions",
    "metadata",
    "source_tracks",
    "transfer_metrics",
    "transfer_runs",
    "transfer_steps",
    "user_overrides",
]
