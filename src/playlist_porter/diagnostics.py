"""Structured diagnostic logging helpers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from loguru import logger

from playlist_porter.matching.status import UnavailableReason
from playlist_porter.models import MatchDecision, TrackCandidate, UniversalTrack


def diagnostic_logger(scope: str):
    """Return a logger bound for file-only diagnostic records."""

    return logger.bind(diagnostic=True, scope=scope)


def track_snapshot(track: UniversalTrack) -> dict[str, Any]:
    """Return safe track metadata for local troubleshooting logs."""

    return {
        "internal_id": str(track.internal_id),
        "platform": track.platform,
        "platform_track_id": track.platform_track_id,
        "title": track.title,
        "artists": list(track.artists),
        "primary_artist": track.primary_artist,
        "album": track.album,
        "isrc": track.isrc,
        "duration_seconds": track.duration_seconds,
        "release_year": track.release_year,
        "explicit": track.explicit,
        "source_playlist_position": track.source_playlist_position,
        "track_fingerprint": track.track_fingerprint,
    }


def track_summary(track: UniversalTrack) -> dict[str, Any]:
    """Return compact track identity for one-line diagnostics."""

    return {
        "title": track.title,
        "artists": list(track.artists),
        "album": track.album,
        "platform": track.platform,
        "platform_track_id": track.platform_track_id,
        "isrc": track.isrc,
        "duration_seconds": track.duration_seconds,
    }


def candidate_snapshot(candidate: TrackCandidate | None) -> dict[str, Any] | None:
    """Return safe candidate details for diagnostic logs."""

    if candidate is None:
        return None
    return {
        "rank": candidate.rank,
        "score": candidate.score,
        "query": candidate.query,
        "track": track_snapshot(candidate.track),
        "evidence": dict(candidate.evidence),
        "unavailable_reason": _reason_value(candidate.unavailable_reason),
    }


def candidate_summary(candidate: TrackCandidate | None) -> dict[str, Any] | None:
    """Return compact candidate details for one-line diagnostics."""

    if candidate is None:
        return None
    return {
        "rank": candidate.rank,
        "score": candidate.score,
        "title": candidate.track.title,
        "artists": list(candidate.track.artists),
        "platform_track_id": candidate.track.platform_track_id,
        "isrc": candidate.track.isrc,
        "duration_seconds": candidate.track.duration_seconds,
        "unavailable_reason": _reason_value(candidate.unavailable_reason),
        "evidence": dict(candidate.evidence),
    }


def decision_snapshot(decision: MatchDecision) -> dict[str, Any]:
    """Return a full safe match decision snapshot."""

    return {
        "source_track": track_snapshot(decision.source_track),
        "status": decision.status.value,
        "score": decision.score,
        "reason_codes": reason_values(decision.reason_codes),
        "evidence": dict(decision.evidence),
        "selected_candidate": candidate_snapshot(decision.selected_candidate),
        "selected_candidate_rank": (
            decision.selected_candidate.rank if decision.selected_candidate is not None else None
        ),
        "candidate_count": len(decision.candidates),
        "candidates": [candidate_snapshot(candidate) for candidate in decision.candidates],
    }


def decision_summary(decision: MatchDecision) -> dict[str, Any]:
    """Return compact decision details for one-line diagnostics."""

    return {
        "source_track": track_summary(decision.source_track),
        "status": decision.status.value,
        "score": decision.score,
        "reason_codes": reason_values(decision.reason_codes),
        "selected_candidate": candidate_summary(decision.selected_candidate),
        "selected_candidate_rank": (
            decision.selected_candidate.rank if decision.selected_candidate is not None else None
        ),
        "candidate_count": len(decision.candidates),
        "evidence": dict(decision.evidence),
    }


def review_update_snapshot(update: Any) -> dict[str, Any]:
    """Return safe review update details without importing the review module."""

    return {
        "source_track_internal_id": str(update.source_track_internal_id),
        "action": update.action,
        "candidate_rank": update.candidate_rank,
        "reason_codes": reason_values(update.reason_codes),
    }


def override_snapshot(override: Any | None) -> dict[str, Any] | None:
    """Return safe persisted override details."""

    if override is None:
        return None
    return {
        "status": override.status.value,
        "selected_candidate_internal_id": override.selected_candidate_internal_id,
        "reason_codes": reason_values(override.reason_codes),
    }


def metrics_snapshot(metrics: Any) -> dict[str, Any]:
    """Return transfer metrics as JSON-friendly diagnostics."""

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
        "status_counts": dict(metrics.status_counts),
        "unavailable_reason_counts": dict(metrics.unavailable_reason_counts),
    }


def preflight_snapshot(result: Any) -> dict[str, Any]:
    """Return preflight inputs and issues."""

    return {
        "source_platform": result.source_platform,
        "destination_platform": result.destination_platform,
        "dry_run": result.dry_run,
        "ok": result.ok,
        "issues": list(result.issues),
    }


def platform_capabilities_snapshot(adapter: Any) -> dict[str, Any]:
    """Return platform name and capability flags."""

    capabilities = adapter.capabilities
    return {
        "platform": adapter.platform_name,
        "supports_read": capabilities.supports_read,
        "supports_search": capabilities.supports_search,
        "supports_write": capabilities.supports_write,
        "supports_isrc": capabilities.supports_isrc,
        "is_official": capabilities.is_official,
    }


def path_values(paths: Iterable[str | Path]) -> list[str]:
    """Return filesystem paths as strings for structured logs."""

    return [str(path) for path in paths]


def reason_values(reasons: Iterable[UnavailableReason]) -> list[str]:
    """Return reason enum values."""

    return [reason.value for reason in reasons]


def write_pair_snapshot(source_track_id: str, destination_track_id: str) -> dict[str, str]:
    """Return one source-to-destination write pair."""

    return {
        "source_track_internal_id": source_track_id,
        "destination_track_id": destination_track_id,
    }


def _reason_value(reason: UnavailableReason | None) -> str | None:
    return reason.value if reason is not None else None


__all__ = [
    "candidate_snapshot",
    "candidate_summary",
    "decision_snapshot",
    "decision_summary",
    "diagnostic_logger",
    "metrics_snapshot",
    "override_snapshot",
    "path_values",
    "platform_capabilities_snapshot",
    "preflight_snapshot",
    "reason_values",
    "review_update_snapshot",
    "track_snapshot",
    "track_summary",
    "write_pair_snapshot",
]
