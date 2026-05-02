"""Deterministic transfer outcome exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from playlist_porter.matching.status import MatchStatus
from playlist_porter.models import MatchDecision
from playlist_porter.persistence.repositories import TransferRepository, UserOverride

SUMMARY_FIELDS = [
    "transfer_run_id",
    "source_track_count",
    "candidate_count",
    "auto_accepted_count",
    "review_required_count",
    "not_found_count",
    "user_approved_count",
    "user_rejected_count",
    "write_success_count",
    "write_failure_count",
    "retry_count",
    "status_counts",
    "unavailable_reason_counts",
]

UNAVAILABLE_FIELDS = [
    "source_track_id",
    "source_title",
    "source_artists",
    "source_album",
    "source_duration_seconds",
    "status",
    "reason_codes",
    "attempted_query",
    "top_suggested_alternates",
    "confidence_scores",
]


def export_reports(
    repository: TransferRepository,
    transfer_run_id: str,
    output_dir: str | Path,
    *,
    output_format: str = "both",
) -> list[Path]:
    """Export summary and unavailable reports and return written paths."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    summary = build_summary(repository, transfer_run_id)
    unavailable_rows = build_unavailable_rows(repository, transfer_run_id)

    written: list[Path] = []
    if output_format in {"json", "both"}:
        summary_path = output_path / "transfer-summary.json"
        unavailable_path = output_path / "unavailable-tracks.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        unavailable_path.write_text(
            json.dumps(unavailable_rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.extend([summary_path, unavailable_path])
    if output_format in {"csv", "both"}:
        summary_path = output_path / "transfer-summary.csv"
        unavailable_path = output_path / "unavailable-tracks.csv"
        _write_csv(summary_path, SUMMARY_FIELDS, [summary])
        _write_csv(unavailable_path, UNAVAILABLE_FIELDS, unavailable_rows)
        written.extend([summary_path, unavailable_path])
    if output_format not in {"csv", "json", "both"}:
        raise ValueError("output_format must be csv, json, or both")
    return written


def build_summary(repository: TransferRepository, transfer_run_id: str) -> dict[str, Any]:
    """Build one exportable metrics summary row."""

    metrics = repository.load_metrics(transfer_run_id)
    return {
        "transfer_run_id": metrics.transfer_run_id,
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
        "status_counts": metrics.status_counts,
        "unavailable_reason_counts": metrics.unavailable_reason_counts,
    }


def build_unavailable_rows(
    repository: TransferRepository,
    transfer_run_id: str,
) -> list[dict[str, Any]]:
    """Build report rows for not-found, unresolved, and rejected tracks."""

    overrides = repository.load_user_overrides(transfer_run_id)
    rows: list[dict[str, Any]] = []
    for decision in repository.load_match_decisions(transfer_run_id):
        override = overrides.get(str(decision.source_track.internal_id))
        if not _is_unavailable_for_report(decision, override):
            continue
        rows.append(_unavailable_row(decision, override))
    return rows


def _is_unavailable_for_report(
    decision: MatchDecision,
    override: UserOverride | None,
) -> bool:
    if override is not None:
        return override.status is MatchStatus.USER_REJECTED
    return decision.status in {
        MatchStatus.NOT_FOUND,
        MatchStatus.METADATA_MEDIUM_CONFIDENCE,
        MatchStatus.NEEDS_REVIEW,
    }


def _unavailable_row(
    decision: MatchDecision,
    override: UserOverride | None,
) -> dict[str, Any]:
    source = decision.source_track
    reason_codes = (
        list(override.reason_codes)
        if override is not None and override.reason_codes
        else decision.reason_codes
    )
    attempted_query = ""
    if decision.candidates and decision.candidates[0].query:
        attempted_query = decision.candidates[0].query or ""
    return {
        "source_track_id": str(source.internal_id),
        "source_title": source.title,
        "source_artists": ";".join(source.artists),
        "source_album": source.album or "",
        "source_duration_seconds": source.duration_seconds,
        "status": override.status.value if override is not None else decision.status.value,
        "reason_codes": ";".join(reason.value for reason in reason_codes),
        "attempted_query": attempted_query,
        "top_suggested_alternates": json.dumps(
            [
                {
                    "rank": candidate.rank,
                    "title": candidate.track.title,
                    "artists": candidate.track.artists,
                    "platform_track_id": candidate.track.platform_track_id,
                }
                for candidate in decision.candidates[:3]
            ],
            sort_keys=True,
        ),
        "confidence_scores": json.dumps(
            [
                {
                    "rank": candidate.rank,
                    "score": candidate.score,
                    "reason": (
                        candidate.unavailable_reason.value
                        if candidate.unavailable_reason is not None
                        else None
                    ),
                }
                for candidate in decision.candidates[:3]
            ],
            sort_keys=True,
        ),
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return ""
    return value


__all__ = [
    "SUMMARY_FIELDS",
    "UNAVAILABLE_FIELDS",
    "build_summary",
    "build_unavailable_rows",
    "export_reports",
]
