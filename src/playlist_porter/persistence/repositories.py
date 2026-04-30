"""Repositories for persisted transfer runs, decisions, metrics, and resume state."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import (
    MatchDecision,
    Playlist,
    TrackCandidate,
    TransferRun,
    UniversalTrack,
)
from playlist_porter.persistence.database import (
    candidate_tracks,
    create_schema,
    create_sqlite_engine,
    match_decisions,
    source_tracks,
    transfer_metrics,
    transfer_runs,
    transfer_steps,
    user_overrides,
)

WRITE_TRACK_STEP = "write_track"


@dataclass(frozen=True)
class TransferMetrics:
    """Aggregate counts for one persisted transfer run."""

    transfer_run_id: str
    source_track_count: int = 0
    candidate_count: int = 0
    auto_accepted_count: int = 0
    review_required_count: int = 0
    not_found_count: int = 0
    user_approved_count: int = 0
    user_rejected_count: int = 0
    write_success_count: int = 0
    write_failure_count: int = 0
    retry_count: int = 0
    elapsed_runtime_seconds: float = 0.0
    status_counts: dict[str, int] = field(default_factory=dict)
    unavailable_reason_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeState:
    """Minimal persisted state needed to skip already-completed write work."""

    transfer_run_id: str
    completed_write_source_track_ids: frozenset[str]
    completed_destination_track_ids: frozenset[str]


@dataclass(frozen=True)
class UserOverride:
    """A persisted manual review decision."""

    source_track_internal_id: str
    status: MatchStatus
    selected_candidate_internal_id: str | None = None
    reason_codes: tuple[UnavailableReason, ...] = ()


class TransferRepository:
    """SQLite-backed repository for transfer run state."""

    def __init__(
        self,
        database_path: str | Path = ":memory:",
        *,
        engine: Engine | None = None,
    ) -> None:
        self.engine = engine or create_sqlite_engine(database_path)
        create_schema(self.engine)

    def create_run(self, run: TransferRun, *, run_key: str | None = None) -> str:
        """Persist a new transfer run and return its id."""

        with self.engine.begin() as connection:
            connection.execute(
                transfer_runs.insert().values(
                    _run_values(run, run_key=run_key or _default_run_key(run))
                )
            )
        self.sync_metrics(str(run.internal_id))
        return str(run.internal_id)

    def get_or_create_run(
        self,
        run: TransferRun,
        *,
        run_key: str | None = None,
    ) -> tuple[str, bool]:
        """Return an existing run for the key or create a new one.

        Use ``create_run`` directly for an explicit restart/new run.
        """

        effective_key = run_key or _default_run_key(run)
        existing = self.find_run_id(effective_key)
        if existing is not None:
            return existing, False
        return self.create_run(run, run_key=effective_key), True

    def find_run_id(self, run_key: str) -> str | None:
        """Find a transfer run by deterministic resume key."""

        with self.engine.connect() as connection:
            row = connection.execute(
                select(transfer_runs.c.internal_id).where(transfer_runs.c.run_key == run_key)
            ).first()
        return str(row.internal_id) if row is not None else None

    def mark_run_completed(
        self,
        transfer_run_id: str | UUID,
        *,
        completed_at: datetime | None = None,
    ) -> None:
        """Mark a transfer run complete and refresh metrics."""

        now = _datetime_to_text(completed_at or _now())
        with self.engine.begin() as connection:
            connection.execute(
                update(transfer_runs)
                .where(transfer_runs.c.internal_id == str(transfer_run_id))
                .values(completed_at=now)
            )
        self.sync_metrics(str(transfer_run_id))

    def save_source_playlist(self, transfer_run_id: str | UUID, playlist: Playlist) -> None:
        """Persist source playlist metadata and ordered source tracks."""

        with self.engine.begin() as connection:
            connection.execute(
                update(transfer_runs)
                .where(transfer_runs.c.internal_id == str(transfer_run_id))
                .values(
                    source_playlist_id=playlist.platform_playlist_id,
                    source_playlist_name=playlist.name,
                )
            )
            for position, track in enumerate(playlist.tracks):
                self._save_source_track(
                    connection,
                    str(transfer_run_id),
                    track,
                    position=track.source_playlist_position or position,
                )
        self.sync_metrics(str(transfer_run_id))

    def save_source_track(
        self,
        transfer_run_id: str | UUID,
        track: UniversalTrack,
        *,
        position: int | None = None,
    ) -> None:
        """Persist or update one source track."""

        with self.engine.begin() as connection:
            self._save_source_track(connection, str(transfer_run_id), track, position=position)
        self.sync_metrics(str(transfer_run_id))

    def load_source_tracks(self, transfer_run_id: str | UUID) -> list[UniversalTrack]:
        """Load source tracks for a run in playlist order."""

        with self.engine.connect() as connection:
            rows = connection.execute(
                select(source_tracks)
                .where(source_tracks.c.transfer_run_id == str(transfer_run_id))
                .order_by(source_tracks.c.position, source_tracks.c.id)
            ).mappings()
            return [_source_track_from_row(row) for row in rows]

    def save_candidates(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
        candidates: list[TrackCandidate],
    ) -> None:
        """Replace persisted candidates for a source track."""

        with self.engine.begin() as connection:
            self._replace_candidates(
                connection,
                str(transfer_run_id),
                str(source_track_id),
                candidates,
            )
        self.sync_metrics(str(transfer_run_id))

    def load_candidates(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
    ) -> list[TrackCandidate]:
        """Load ranked candidate tracks for a source track."""

        with self.engine.connect() as connection:
            rows = connection.execute(
                select(candidate_tracks)
                .where(candidate_tracks.c.transfer_run_id == str(transfer_run_id))
                .where(candidate_tracks.c.source_track_internal_id == str(source_track_id))
                .order_by(candidate_tracks.c.rank, candidate_tracks.c.id)
            ).mappings()
            return [_candidate_from_row(row) for row in rows]

    def save_match_decision(
        self,
        transfer_run_id: str | UUID,
        decision: MatchDecision,
    ) -> None:
        """Persist one match decision and its candidates."""

        with self.engine.begin() as connection:
            self._save_decision(connection, str(transfer_run_id), decision)
        self.sync_metrics(str(transfer_run_id))

    def save_match_decisions(
        self,
        transfer_run_id: str | UUID,
        decisions: list[MatchDecision],
    ) -> None:
        """Persist a batch of match decisions and refresh metrics once."""

        with self.engine.begin() as connection:
            for decision in decisions:
                self._save_decision(connection, str(transfer_run_id), decision)
        self.sync_metrics(str(transfer_run_id))

    def load_match_decisions(self, transfer_run_id: str | UUID) -> list[MatchDecision]:
        """Load persisted match decisions in source playlist order."""

        with self.engine.connect() as connection:
            rows = connection.execute(
                select(match_decisions, source_tracks.c.position)
                .join(
                    source_tracks,
                    (source_tracks.c.transfer_run_id == match_decisions.c.transfer_run_id)
                    & (
                        source_tracks.c.internal_id
                        == match_decisions.c.source_track_internal_id
                    ),
                )
                .where(match_decisions.c.transfer_run_id == str(transfer_run_id))
                .order_by(source_tracks.c.position, match_decisions.c.id)
            ).mappings()
            decision_rows = list(rows)

        return [
            self._decision_from_row(str(transfer_run_id), row)
            for row in decision_rows
        ]

    def save_user_override(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
        *,
        status: MatchStatus,
        selected_candidate: TrackCandidate | None = None,
        reason_codes: list[UnavailableReason] | None = None,
    ) -> None:
        """Persist a manual review decision for later reuse."""

        now = _datetime_to_text(_now())
        selected_id = (
            str(selected_candidate.track.internal_id)
            if selected_candidate is not None
            else None
        )
        values = {
            "transfer_run_id": str(transfer_run_id),
            "source_track_internal_id": str(source_track_id),
            "status": status.value,
            "selected_candidate_internal_id": selected_id,
            "reason_codes_json": _json_dumps([reason.value for reason in reason_codes or []]),
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as connection:
            statement = sqlite_insert(user_overrides).values(values)
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        user_overrides.c.transfer_run_id,
                        user_overrides.c.source_track_internal_id,
                    ],
                    set_={
                        "status": statement.excluded.status,
                        "selected_candidate_internal_id": (
                            statement.excluded.selected_candidate_internal_id
                        ),
                        "reason_codes_json": statement.excluded.reason_codes_json,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )
        self.sync_metrics(str(transfer_run_id))

    def load_user_override(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
    ) -> UserOverride | None:
        """Load a persisted manual review decision."""

        with self.engine.connect() as connection:
            row = connection.execute(
                select(user_overrides)
                .where(user_overrides.c.transfer_run_id == str(transfer_run_id))
                .where(user_overrides.c.source_track_internal_id == str(source_track_id))
            ).mappings().first()

        if row is None:
            return None
        return UserOverride(
            source_track_internal_id=row["source_track_internal_id"],
            status=MatchStatus(row["status"]),
            selected_candidate_internal_id=row["selected_candidate_internal_id"],
            reason_codes=tuple(
                UnavailableReason(reason)
                for reason in _json_loads(row["reason_codes_json"], default=[])
            ),
        )

    def record_write_success(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
        destination_track_id: str,
        *,
        step_type: str = WRITE_TRACK_STEP,
    ) -> None:
        """Record a completed destination write step idempotently."""

        self._record_write_step(
            transfer_run_id,
            source_track_id,
            destination_track_id,
            step_type=step_type,
            status="completed",
        )

    def record_write_failure(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
        destination_track_id: str,
        *,
        error: str | None = None,
        retry_count: int = 0,
        step_type: str = WRITE_TRACK_STEP,
    ) -> None:
        """Record a failed destination write attempt."""

        self._record_write_step(
            transfer_run_id,
            source_track_id,
            destination_track_id,
            step_type=step_type,
            status="failed",
            error=error,
            retry_count=retry_count,
        )

    def should_write_track(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
        destination_track_id: str,
        *,
        step_type: str = WRITE_TRACK_STEP,
    ) -> bool:
        """Return false when a successful write was already recorded."""

        with self.engine.connect() as connection:
            row = connection.execute(
                select(transfer_steps.c.id)
                .where(transfer_steps.c.transfer_run_id == str(transfer_run_id))
                .where(transfer_steps.c.step_type == step_type)
                .where(transfer_steps.c.source_track_internal_id == str(source_track_id))
                .where(transfer_steps.c.destination_track_id == destination_track_id)
                .where(transfer_steps.c.status == "completed")
            ).first()
        return row is None

    def pending_write_track_ids(
        self,
        transfer_run_id: str | UUID,
        destination_track_ids: list[str],
        *,
        step_type: str = WRITE_TRACK_STEP,
    ) -> list[str]:
        """Filter destination ids to those not already completed for the run."""

        resume = self.get_resume_state(transfer_run_id, step_type=step_type)
        return [
            track_id
            for track_id in destination_track_ids
            if track_id not in resume.completed_destination_track_ids
        ]

    def get_resume_state(
        self,
        transfer_run_id: str | UUID,
        *,
        step_type: str = WRITE_TRACK_STEP,
    ) -> ResumeState:
        """Load completed write markers for resume."""

        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    transfer_steps.c.source_track_internal_id,
                    transfer_steps.c.destination_track_id,
                )
                .where(transfer_steps.c.transfer_run_id == str(transfer_run_id))
                .where(transfer_steps.c.step_type == step_type)
                .where(transfer_steps.c.status == "completed")
            ).mappings()
            completed_source_ids: set[str] = set()
            completed_destination_ids: set[str] = set()
            for row in rows:
                if row["source_track_internal_id"] is not None:
                    completed_source_ids.add(row["source_track_internal_id"])
                if row["destination_track_id"] is not None:
                    completed_destination_ids.add(row["destination_track_id"])

        return ResumeState(
            transfer_run_id=str(transfer_run_id),
            completed_write_source_track_ids=frozenset(completed_source_ids),
            completed_destination_track_ids=frozenset(completed_destination_ids),
        )

    def sync_metrics(self, transfer_run_id: str | UUID) -> TransferMetrics:
        """Recompute and store aggregate transfer metrics."""

        run_id = str(transfer_run_id)
        with self.engine.begin() as connection:
            metrics = self._compute_metrics(connection, run_id)
            values = {
                "transfer_run_id": run_id,
                "source_track_count": metrics.source_track_count,
                "candidate_count": metrics.candidate_count,
                "auto_accepted_count": metrics.auto_accepted_count,
                "review_required_count": metrics.review_required_count,
                "not_found_count": metrics.not_found_count,
                "user_approved_count": metrics.user_approved_count,
                "user_rejected_count": metrics.user_rejected_count,
                "write_success_count": metrics.write_success_count,
                "write_failure_count": metrics.write_failure_count,
                "retry_count": metrics.retry_count,
                "elapsed_runtime_seconds": metrics.elapsed_runtime_seconds,
                "status_counts_json": _json_dumps(metrics.status_counts),
                "unavailable_reason_counts_json": _json_dumps(
                    metrics.unavailable_reason_counts
                ),
                "updated_at": _datetime_to_text(_now()),
            }
            statement = sqlite_insert(transfer_metrics).values(values)
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[transfer_metrics.c.transfer_run_id],
                    set_={
                        column.name: getattr(statement.excluded, column.name)
                        for column in transfer_metrics.c
                        if column.name != "transfer_run_id"
                    },
                )
            )
        return metrics

    def load_metrics(self, transfer_run_id: str | UUID) -> TransferMetrics:
        """Load stored transfer metrics, recomputing them if needed."""

        run_id = str(transfer_run_id)
        with self.engine.connect() as connection:
            row = connection.execute(
                select(transfer_metrics).where(transfer_metrics.c.transfer_run_id == run_id)
            ).mappings().first()
        if row is None:
            return self.sync_metrics(run_id)
        return _metrics_from_row(row)

    def _save_source_track(
        self,
        connection: Connection,
        transfer_run_id: str,
        track: UniversalTrack,
        *,
        position: int | None,
    ) -> None:
        values = _source_track_values(transfer_run_id, track, position=position)
        statement = sqlite_insert(source_tracks).values(values)
        update_values = {
            column.name: getattr(statement.excluded, column.name)
            for column in source_tracks.c
            if column.name not in {"id", "transfer_run_id", "internal_id", "position"}
        }
        update_values["position"] = func.coalesce(
            statement.excluded.position,
            source_tracks.c.position,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    source_tracks.c.transfer_run_id,
                    source_tracks.c.internal_id,
                ],
                set_=update_values,
            )
        )

    def _replace_candidates(
        self,
        connection: Connection,
        transfer_run_id: str,
        source_track_id: str,
        candidates: list[TrackCandidate],
    ) -> None:
        connection.execute(
            delete(candidate_tracks)
            .where(candidate_tracks.c.transfer_run_id == transfer_run_id)
            .where(candidate_tracks.c.source_track_internal_id == source_track_id)
        )
        for candidate in candidates:
            connection.execute(
                candidate_tracks.insert().values(
                    _candidate_values(transfer_run_id, source_track_id, candidate)
                )
            )

    def _save_decision(
        self,
        connection: Connection,
        transfer_run_id: str,
        decision: MatchDecision,
    ) -> None:
        source_track_id = str(decision.source_track.internal_id)
        self._save_source_track(
            connection,
            transfer_run_id,
            decision.source_track,
            position=decision.source_track.source_playlist_position,
        )
        self._replace_candidates(connection, transfer_run_id, source_track_id, decision.candidates)
        selected_id = (
            str(decision.selected_candidate.track.internal_id)
            if decision.selected_candidate is not None
            else None
        )
        values = {
            "transfer_run_id": transfer_run_id,
            "source_track_internal_id": source_track_id,
            "status": decision.status.value,
            "selected_candidate_internal_id": selected_id,
            "score": decision.score,
            "evidence_json": _json_dumps(decision.evidence),
            "reason_codes_json": _json_dumps(
                [reason.value for reason in decision.reason_codes]
            ),
            "updated_at": _datetime_to_text(_now()),
        }
        connection.execute(
            delete(match_decisions)
            .where(match_decisions.c.transfer_run_id == transfer_run_id)
            .where(match_decisions.c.source_track_internal_id == source_track_id)
        )
        connection.execute(match_decisions.insert().values(values))

    def _decision_from_row(self, transfer_run_id: str, row: RowMapping) -> MatchDecision:
        source_track = self._load_source_track(transfer_run_id, row["source_track_internal_id"])
        candidates = self.load_candidates(transfer_run_id, row["source_track_internal_id"])
        selected_candidate = next(
            (
                candidate
                for candidate in candidates
                if str(candidate.track.internal_id) == row["selected_candidate_internal_id"]
            ),
            None,
        )
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus(row["status"]),
            candidates=candidates,
            selected_candidate=selected_candidate,
            score=row["score"],
            evidence=_json_loads(row["evidence_json"], default={}),
            reason_codes=[
                UnavailableReason(reason)
                for reason in _json_loads(row["reason_codes_json"], default=[])
            ],
        )

    def _load_source_track(
        self,
        transfer_run_id: str,
        source_track_id: str,
    ) -> UniversalTrack:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(source_tracks)
                .where(source_tracks.c.transfer_run_id == transfer_run_id)
                .where(source_tracks.c.internal_id == source_track_id)
            ).mappings().one()
        return _source_track_from_row(row)

    def _record_write_step(
        self,
        transfer_run_id: str | UUID,
        source_track_id: str | UUID,
        destination_track_id: str,
        *,
        step_type: str,
        status: str,
        error: str | None = None,
        retry_count: int = 0,
    ) -> None:
        run_id = str(transfer_run_id)
        source_id = str(source_track_id)
        now = _datetime_to_text(_now())
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(transfer_steps)
                .where(transfer_steps.c.transfer_run_id == run_id)
                .where(transfer_steps.c.step_type == step_type)
                .where(transfer_steps.c.source_track_internal_id == source_id)
                .where(transfer_steps.c.destination_track_id == destination_track_id)
            ).mappings().first()
            if existing is not None and existing["status"] == "completed":
                return

            values = {
                "transfer_run_id": run_id,
                "step_type": step_type,
                "source_track_internal_id": source_id,
                "destination_track_id": destination_track_id,
                "status": status,
                "attempt_count": (existing["attempt_count"] if existing else 0) + 1,
                "retry_count": (existing["retry_count"] if existing else 0) + retry_count,
                "error": error,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
                "completed_at": now if status == "completed" else None,
            }
            if existing is None:
                connection.execute(transfer_steps.insert().values(values))
            else:
                connection.execute(
                    update(transfer_steps)
                    .where(transfer_steps.c.id == existing["id"])
                    .values(values)
                )
        self.sync_metrics(run_id)

    def _compute_metrics(self, connection: Connection, transfer_run_id: str) -> TransferMetrics:
        source_track_count = _count(
            connection,
            source_tracks,
            source_tracks.c.transfer_run_id == transfer_run_id,
        )
        candidate_count = _count(
            connection,
            candidate_tracks,
            candidate_tracks.c.transfer_run_id == transfer_run_id,
        )
        decision_rows = list(
            connection.execute(
                select(match_decisions).where(
                    match_decisions.c.transfer_run_id == transfer_run_id
                )
            ).mappings()
        )
        override_rows = list(
            connection.execute(
                select(user_overrides).where(user_overrides.c.transfer_run_id == transfer_run_id)
            ).mappings()
        )
        step_rows = list(
            connection.execute(
                select(transfer_steps).where(transfer_steps.c.transfer_run_id == transfer_run_id)
            ).mappings()
        )
        run_row = connection.execute(
            select(transfer_runs).where(transfer_runs.c.internal_id == transfer_run_id)
        ).mappings().one()

        status_counts = Counter(row["status"] for row in decision_rows)
        unavailable_reason_counts: Counter[str] = Counter()
        for row in decision_rows:
            unavailable_reason_counts.update(_json_loads(row["reason_codes_json"], default=[]))

        override_status_counts = Counter(row["status"] for row in override_rows)
        write_success_count = sum(1 for row in step_rows if row["status"] == "completed")
        write_failure_count = sum(1 for row in step_rows if row["status"] == "failed")
        retry_count = sum(row["retry_count"] or 0 for row in step_rows)

        auto_accepted_count = (
            status_counts[MatchStatus.ISRC_EXACT.value]
            + status_counts[MatchStatus.METADATA_HIGH_CONFIDENCE.value]
        )
        review_required_count = (
            status_counts[MatchStatus.METADATA_MEDIUM_CONFIDENCE.value]
            + status_counts[MatchStatus.NEEDS_REVIEW.value]
        )

        return TransferMetrics(
            transfer_run_id=transfer_run_id,
            source_track_count=source_track_count,
            candidate_count=candidate_count,
            auto_accepted_count=auto_accepted_count,
            review_required_count=review_required_count,
            not_found_count=status_counts[MatchStatus.NOT_FOUND.value],
            user_approved_count=override_status_counts[MatchStatus.USER_APPROVED.value],
            user_rejected_count=override_status_counts[MatchStatus.USER_REJECTED.value],
            write_success_count=write_success_count,
            write_failure_count=write_failure_count,
            retry_count=retry_count,
            elapsed_runtime_seconds=_elapsed_seconds(run_row),
            status_counts=dict(status_counts),
            unavailable_reason_counts=dict(unavailable_reason_counts),
        )


def _run_values(run: TransferRun, *, run_key: str) -> dict[str, Any]:
    playlist = run.source_playlist
    return {
        "internal_id": str(run.internal_id),
        "run_key": run_key,
        "source_platform": run.source_platform,
        "destination_platform": run.destination_platform,
        "source_playlist_id": playlist.platform_playlist_id if playlist else None,
        "source_playlist_name": playlist.name if playlist else None,
        "destination_playlist_id": run.destination_playlist_id,
        "dry_run": run.dry_run,
        "started_at": _datetime_to_text(run.started_at),
        "completed_at": _datetime_to_text(run.completed_at) if run.completed_at else None,
        "metadata_json": _json_dumps(run.metadata),
    }


def _source_track_values(
    transfer_run_id: str,
    track: UniversalTrack,
    *,
    position: int | None,
) -> dict[str, Any]:
    return {
        "transfer_run_id": transfer_run_id,
        "internal_id": str(track.internal_id),
        "position": position,
        **_track_values(track),
    }


def _candidate_values(
    transfer_run_id: str,
    source_track_id: str,
    candidate: TrackCandidate,
) -> dict[str, Any]:
    return {
        "transfer_run_id": transfer_run_id,
        "source_track_internal_id": source_track_id,
        "candidate_internal_id": str(candidate.track.internal_id),
        "rank": candidate.rank,
        "query": candidate.query,
        "score": candidate.score,
        "unavailable_reason": (
            candidate.unavailable_reason.value
            if candidate.unavailable_reason is not None
            else None
        ),
        "evidence_json": _json_dumps(candidate.evidence),
        **_track_values(candidate.track),
    }


def _track_values(track: UniversalTrack) -> dict[str, Any]:
    return {
        "platform": track.platform,
        "platform_track_id": track.platform_track_id,
        "title": track.title,
        "artists_json": _json_dumps(track.artists),
        "album": track.album,
        "isrc": track.isrc,
        "duration_seconds": track.duration_seconds,
        "release_date": track.release_date.isoformat() if track.release_date else None,
        "release_year": track.release_year,
        "explicit": track.explicit,
        "track_fingerprint": track.track_fingerprint,
    }


def _source_track_from_row(row: RowMapping) -> UniversalTrack:
    return UniversalTrack(
        internal_id=UUID(row["internal_id"]),
        title=row["title"],
        artists=_json_loads(row["artists_json"], default=[]),
        platform=row["platform"],
        platform_track_id=row["platform_track_id"],
        album=row["album"],
        isrc=row["isrc"],
        duration_seconds=row["duration_seconds"],
        release_date=row["release_date"],
        release_year=row["release_year"],
        explicit=row["explicit"],
        source_playlist_position=row["position"],
    )


def _candidate_from_row(row: RowMapping) -> TrackCandidate:
    track = UniversalTrack(
        internal_id=UUID(row["candidate_internal_id"]),
        title=row["title"],
        artists=_json_loads(row["artists_json"], default=[]),
        platform=row["platform"],
        platform_track_id=row["platform_track_id"],
        album=row["album"],
        isrc=row["isrc"],
        duration_seconds=row["duration_seconds"],
        release_date=row["release_date"],
        release_year=row["release_year"],
        explicit=row["explicit"],
    )
    reason = row["unavailable_reason"]
    return TrackCandidate(
        track=track,
        score=row["score"],
        rank=row["rank"],
        query=row["query"],
        evidence=_json_loads(row["evidence_json"], default={}),
        unavailable_reason=UnavailableReason(reason) if reason else None,
    )


def _metrics_from_row(row: RowMapping) -> TransferMetrics:
    return TransferMetrics(
        transfer_run_id=row["transfer_run_id"],
        source_track_count=row["source_track_count"],
        candidate_count=row["candidate_count"],
        auto_accepted_count=row["auto_accepted_count"],
        review_required_count=row["review_required_count"],
        not_found_count=row["not_found_count"],
        user_approved_count=row["user_approved_count"],
        user_rejected_count=row["user_rejected_count"],
        write_success_count=row["write_success_count"],
        write_failure_count=row["write_failure_count"],
        retry_count=row["retry_count"],
        elapsed_runtime_seconds=row["elapsed_runtime_seconds"],
        status_counts=_json_loads(row["status_counts_json"], default={}),
        unavailable_reason_counts=_json_loads(
            row["unavailable_reason_counts_json"],
            default={},
        ),
    )


def _default_run_key(run: TransferRun) -> str:
    playlist = run.source_playlist
    source_playlist_key = ""
    if playlist is not None:
        source_playlist_key = playlist.platform_playlist_id or str(playlist.internal_id)
    mode = "dry-run" if run.dry_run else "write"
    return "|".join(
        [
            run.source_platform,
            run.destination_platform,
            source_playlist_key,
            run.destination_playlist_id or "",
            mode,
        ]
    )


def _count(connection: Connection, table: Any, whereclause: Any) -> int:
    count = connection.execute(
        select(func.count()).select_from(table).where(whereclause)
    ).scalar_one()
    return int(count)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_loads(value: str | None, *, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _now() -> datetime:
    return datetime.now(UTC)


def _datetime_to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _elapsed_seconds(run_row: RowMapping) -> float:
    started_at = _parse_datetime(run_row["started_at"])
    completed_at = (
        _parse_datetime(run_row["completed_at"])
        if run_row["completed_at"] is not None
        else _now()
    )
    return max((completed_at - started_at).total_seconds(), 0.0)


__all__ = [
    "ResumeState",
    "TransferMetrics",
    "TransferRepository",
    "UserOverride",
    "WRITE_TRACK_STEP",
]
