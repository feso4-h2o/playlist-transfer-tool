"""Terminal review helpers for persisted match decisions."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from playlist_porter.diagnostics import (
    decision_summary,
    diagnostic_logger,
    review_update_snapshot,
)
from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import MatchDecision, TrackCandidate, UniversalTrack
from playlist_porter.persistence.repositories import TransferRepository

REVIEW_DIAGNOSTICS = diagnostic_logger("review")

REVIEWABLE_STATUSES = {
    MatchStatus.METADATA_MEDIUM_CONFIDENCE,
    MatchStatus.NEEDS_REVIEW,
    MatchStatus.NOT_FOUND,
}


@dataclass(frozen=True)
class ReviewUpdate:
    """One manual review update to persist."""

    source_track_internal_id: str
    action: str
    candidate_rank: int | None = None
    reason_codes: tuple[UnavailableReason, ...] = ()


def reviewable_decisions(decisions: list[MatchDecision]) -> list[MatchDecision]:
    """Return decisions that should be shown for manual review."""

    reviewable = [decision for decision in decisions if decision.status in REVIEWABLE_STATUSES]
    REVIEW_DIAGNOSTICS.debug(
        "reviewable decisions filtered",
        decision_count=len(decisions),
        reviewable_count=len(reviewable),
        statuses=[decision.status.value for decision in decisions],
    )
    return reviewable


def apply_review_update(
    repository: TransferRepository,
    transfer_run_id: str,
    update: ReviewUpdate,
) -> None:
    """Persist one accept/reject review update."""

    REVIEW_DIAGNOSTICS.debug(
        "review update requested",
        run_id=transfer_run_id,
        update=review_update_snapshot(update),
    )
    decision = _find_decision(repository, transfer_run_id, update.source_track_internal_id)
    action = update.action.casefold()
    if action == "accept":
        candidate = _candidate_by_rank(decision, update.candidate_rank or 1)
        REVIEW_DIAGNOSTICS.debug(
            "review candidate accepted",
            run_id=transfer_run_id,
            update=review_update_snapshot(update),
            decision=decision_summary(decision),
            selected_candidate_rank=candidate.rank,
        )
        repository.save_user_override(
            transfer_run_id,
            update.source_track_internal_id,
            status=MatchStatus.USER_APPROVED,
            selected_candidate=candidate,
        )
        return
    if action == "reject":
        REVIEW_DIAGNOSTICS.debug(
            "review candidate rejected",
            run_id=transfer_run_id,
            update=review_update_snapshot(update),
            decision=decision_summary(decision),
            reason_codes=[
                reason.value for reason in (list(update.reason_codes) or decision.reason_codes)
            ],
        )
        repository.save_user_override(
            transfer_run_id,
            update.source_track_internal_id,
            status=MatchStatus.USER_REJECTED,
            reason_codes=list(update.reason_codes) or decision.reason_codes,
        )
        return
    if action == "skip":
        REVIEW_DIAGNOSTICS.debug(
            "review update skipped",
            run_id=transfer_run_id,
            update=review_update_snapshot(update),
            decision=decision_summary(decision),
        )
        return
    REVIEW_DIAGNOSTICS.debug(
        "review update invalid action",
        run_id=transfer_run_id,
        update=review_update_snapshot(update),
        decision=decision_summary(decision),
    )
    raise ValueError(f"unknown review action: {update.action}")


def run_interactive_review(
    repository: TransferRepository,
    transfer_run_id: str,
    *,
    console: Console | None = None,
) -> int:
    """Run a simple Rich prompt loop and return the number of saved overrides."""

    console = console or Console()
    decisions = reviewable_decisions(repository.load_match_decisions(transfer_run_id))
    REVIEW_DIAGNOSTICS.debug(
        "interactive review loaded decisions",
        run_id=transfer_run_id,
        reviewable_count=len(decisions),
    )
    saved_count = 0
    for decision in decisions:
        _render_decision(console, decision)
        action = Prompt.ask(
            "Action",
            choices=["accept", "reject", "skip"],
            default="skip",
            console=console,
        )
        if action == "accept":
            rank_text = Prompt.ask("Candidate rank", default="1", console=console)
            update = ReviewUpdate(
                source_track_internal_id=str(decision.source_track.internal_id),
                action=action,
                candidate_rank=int(rank_text),
            )
        else:
            update = ReviewUpdate(
                source_track_internal_id=str(decision.source_track.internal_id),
                action=action,
            )
        apply_review_update(repository, transfer_run_id, update)
        if action != "skip":
            saved_count += 1
        REVIEW_DIAGNOSTICS.debug(
            "interactive review action processed",
            run_id=transfer_run_id,
            update=review_update_snapshot(update),
            saved_count=saved_count,
        )
    REVIEW_DIAGNOSTICS.debug(
        "interactive review completed",
        run_id=transfer_run_id,
        saved_count=saved_count,
        reviewable_count=len(decisions),
    )
    return saved_count


def _find_decision(
    repository: TransferRepository,
    transfer_run_id: str,
    source_track_id: str,
) -> MatchDecision:
    for decision in repository.load_match_decisions(transfer_run_id):
        if str(decision.source_track.internal_id) == source_track_id:
            return decision
    raise ValueError(f"source track not found in run: {source_track_id}")


def _candidate_by_rank(decision: MatchDecision, rank: int) -> TrackCandidate:
    for candidate in decision.candidates:
        if candidate.rank == rank:
            return candidate
    REVIEW_DIAGNOSTICS.debug(
        "review candidate rank not found",
        decision=decision_summary(decision),
        requested_rank=rank,
    )
    raise ValueError(f"candidate rank {rank} not found for {decision.source_track.title}")


def _render_decision(console: Console, decision: MatchDecision) -> None:
    source = decision.source_track
    REVIEW_DIAGNOSTICS.debug("review decision rendered", decision=decision_summary(decision))
    console.print(f"\n[bold]{source.title}[/bold] - {', '.join(source.artists)}")
    console.print(
        f"status={decision.status.value} score={_decision_score_text(decision.score)} "
        f"reasons={','.join(reason.value for reason in decision.reason_codes) or '-'}"
    )
    table = Table("Rank", "Track", "Score", "Metadata", "IDs", "Reasons")
    top_score = decision.candidates[0].score if decision.candidates else None
    for candidate in decision.candidates:
        table.add_row(
            str(candidate.rank),
            _candidate_identity(candidate),
            _score_text(candidate, top_score),
            _candidate_metadata(candidate),
            _candidate_ids(candidate),
            _candidate_reason_text(candidate),
        )
    console.print(table)


def _candidate_identity(candidate: TrackCandidate) -> str:
    track = candidate.track
    return f"{track.title}\n{', '.join(track.artists)}"


def _decision_score_text(score: float | None) -> str:
    return f"{score:.4f}" if score is not None else "-"


def _score_text(candidate: TrackCandidate, top_score: float | None) -> str:
    score = f"{candidate.score:.4f}"
    if top_score is None:
        return score
    delta = top_score - candidate.score
    return f"{score}\ndelta={delta:.4f}"


def _candidate_metadata(candidate: TrackCandidate) -> str:
    track = candidate.track
    values = [
        ("album", track.album),
        ("duration", _duration_text(track.duration_seconds)),
        ("release", _release_text(track)),
        ("explicit", _explicit_text(track.explicit)),
    ]
    return _joined_fields(values)


def _candidate_ids(candidate: TrackCandidate) -> str:
    track = candidate.track
    return _joined_fields(
        [
            ("isrc", track.isrc),
            ("id", track.platform_track_id),
            ("url", _destination_url(track.platform, track.platform_track_id)),
        ]
    )


def _candidate_reason_text(candidate: TrackCandidate) -> str:
    reasons: list[str] = []
    if candidate.unavailable_reason is not None:
        reasons.append(candidate.unavailable_reason.value)
    evidence_reasons = candidate.evidence.get("reason_codes")
    if isinstance(evidence_reasons, str):
        reasons.extend(reason for reason in evidence_reasons.split(",") if reason)
    return ", ".join(dict.fromkeys(reasons)) or "-"


def _joined_fields(values: list[tuple[str, str | None]]) -> str:
    fields = [f"{name}={value}" for name, value in values if value not in {None, ""}]
    return "\n".join(fields) or "-"


def _duration_text(duration_seconds: int | None) -> str | None:
    if duration_seconds is None:
        return None
    minutes, seconds = divmod(duration_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _release_text(track: UniversalTrack) -> str | None:
    if track.release_date is not None:
        return track.release_date.isoformat()
    if track.release_year is not None:
        return str(track.release_year)
    return None


def _explicit_text(explicit: bool | None) -> str | None:
    if explicit is None:
        return None
    return "yes" if explicit else "no"


def _destination_url(platform: str | None, platform_track_id: str | None) -> str | None:
    if platform is None or platform_track_id is None:
        return None
    normalized_platform = platform.casefold()
    if normalized_platform == "spotify":
        track_id = platform_track_id.removeprefix("spotify:track:")
        return f"https://open.spotify.com/track/{track_id}"
    if normalized_platform == "qqmusic" and _is_qqmusic_songmid(platform_track_id):
        return f"https://y.qq.com/n/ryqq/songDetail/{platform_track_id}"
    return None


def _is_qqmusic_songmid(platform_track_id: str) -> bool:
    return ":" not in platform_track_id and not platform_track_id.isdigit()


__all__ = [
    "REVIEWABLE_STATUSES",
    "ReviewUpdate",
    "apply_review_update",
    "reviewable_decisions",
    "run_interactive_review",
]
