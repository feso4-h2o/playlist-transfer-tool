"""Deterministic transfer outcome exports."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from playlist_porter.diagnostics import diagnostic_logger, metrics_snapshot, path_values
from playlist_porter.matching.status import MatchStatus
from playlist_porter.models import MatchDecision
from playlist_porter.persistence.repositories import TransferRepository, UserOverride

EXPORT_DIAGNOSTICS = diagnostic_logger("export")

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

    if output_format not in {"csv", "json", "both"}:
        raise ValueError("output_format must be csv, json, or both")

    output_path = Path(output_dir) / _short_run_id(transfer_run_id)
    output_path.mkdir(parents=True, exist_ok=True)
    summary = build_summary(repository, transfer_run_id)
    unavailable_rows = build_unavailable_rows(repository, transfer_run_id)
    paths = _report_paths(output_path, output_format, _short_timestamp())
    EXPORT_DIAGNOSTICS.debug(
        "report export prepared",
        run_id=transfer_run_id,
        output_dir=str(output_path),
        output_format=output_format,
        summary=summary,
        unavailable_row_count=len(unavailable_rows),
        planned_paths={key: str(path) for key, path in paths.items()},
    )

    written: list[Path] = []
    if output_format in {"json", "both"}:
        summary_path = paths["summary_json"]
        unavailable_path = paths["unavailable_json"]
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        unavailable_path.write_text(
            json.dumps(unavailable_rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.extend([summary_path, unavailable_path])
        EXPORT_DIAGNOSTICS.debug(
            "json reports written",
            run_id=transfer_run_id,
            paths=path_values([summary_path, unavailable_path]),
            unavailable_row_count=len(unavailable_rows),
        )
    if output_format in {"csv", "both"}:
        summary_path = paths["summary_csv"]
        unavailable_path = paths["unavailable_csv"]
        _write_csv(summary_path, SUMMARY_FIELDS, [summary])
        _write_csv(unavailable_path, UNAVAILABLE_FIELDS, unavailable_rows)
        written.extend([summary_path, unavailable_path])
        EXPORT_DIAGNOSTICS.debug(
            "csv reports written",
            run_id=transfer_run_id,
            paths=path_values([summary_path, unavailable_path]),
            unavailable_row_count=len(unavailable_rows),
        )
    EXPORT_DIAGNOSTICS.debug(
        "report export completed",
        run_id=transfer_run_id,
        written_paths=path_values(written),
    )
    return written


def build_summary(repository: TransferRepository, transfer_run_id: str) -> dict[str, Any]:
    """Build one exportable metrics summary row."""

    metrics = repository.load_metrics(transfer_run_id)
    EXPORT_DIAGNOSTICS.debug(
        "report summary built",
        run_id=transfer_run_id,
        metrics=metrics_snapshot(metrics),
    )
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
    EXPORT_DIAGNOSTICS.debug(
        "unavailable report rows built",
        run_id=transfer_run_id,
        override_count=len(overrides),
        row_count=len(rows),
    )
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


def _report_paths(output_path: Path, output_format: str, timestamp: str) -> dict[str, Path]:
    suffix = timestamp
    counter = 2
    paths = _paths_for_suffix(output_path, suffix)
    while _has_existing_batch_file(paths):
        suffix = f"{timestamp}-{counter}"
        counter += 1
        paths = _paths_for_suffix(output_path, suffix)

    if output_format == "json":
        return {key: value for key, value in paths.items() if key.endswith("_json")}
    if output_format == "csv":
        return {key: value for key, value in paths.items() if key.endswith("_csv")}
    return paths


def _paths_for_suffix(output_path: Path, suffix: str) -> dict[str, Path]:
    return {
        "summary_json": output_path / f"transfer-summary-{suffix}.json",
        "unavailable_json": output_path / f"unavailable-tracks-{suffix}.json",
        "summary_csv": output_path / f"transfer-summary-{suffix}.csv",
        "unavailable_csv": output_path / f"unavailable-tracks-{suffix}.csv",
    }


def _has_existing_batch_file(paths: dict[str, Path]) -> bool:
    return any(path.exists() for path in paths.values())


def _short_run_id(transfer_run_id: str) -> str:
    return transfer_run_id[:8]


def _short_timestamp() -> str:
    return datetime.now().strftime("%H%M%S")


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
