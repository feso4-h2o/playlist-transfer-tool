"""Terminal review helpers for persisted match decisions."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import MatchDecision, TrackCandidate
from playlist_porter.persistence.repositories import TransferRepository

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

    return [decision for decision in decisions if decision.status in REVIEWABLE_STATUSES]


def apply_review_update(
    repository: TransferRepository,
    transfer_run_id: str,
    update: ReviewUpdate,
) -> None:
    """Persist one accept/reject review update."""

    decision = _find_decision(repository, transfer_run_id, update.source_track_internal_id)
    action = update.action.casefold()
    if action == "accept":
        candidate = _candidate_by_rank(decision, update.candidate_rank or 1)
        repository.save_user_override(
            transfer_run_id,
            update.source_track_internal_id,
            status=MatchStatus.USER_APPROVED,
            selected_candidate=candidate,
        )
        return
    if action == "reject":
        repository.save_user_override(
            transfer_run_id,
            update.source_track_internal_id,
            status=MatchStatus.USER_REJECTED,
            reason_codes=list(update.reason_codes) or decision.reason_codes,
        )
        return
    if action == "skip":
        return
    raise ValueError(f"unknown review action: {update.action}")


def run_interactive_review(
    repository: TransferRepository,
    transfer_run_id: str,
    *,
    console: Console | None = None,
) -> int:
    """Run a simple Rich prompt loop and return the number of saved overrides."""

    console = console or Console()
    saved_count = 0
    for decision in reviewable_decisions(repository.load_match_decisions(transfer_run_id)):
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
    raise ValueError(f"candidate rank {rank} not found for {decision.source_track.title}")


def _render_decision(console: Console, decision: MatchDecision) -> None:
    source = decision.source_track
    console.print(f"\n[bold]{source.title}[/bold] - {', '.join(source.artists)}")
    console.print(
        f"status={decision.status.value} score={decision.score} "
        f"reasons={','.join(reason.value for reason in decision.reason_codes) or '-'}"
    )
    table = Table("Rank", "Title", "Artists", "Score", "Reasons")
    for candidate in decision.candidates:
        table.add_row(
            str(candidate.rank),
            candidate.track.title,
            ", ".join(candidate.track.artists),
            f"{candidate.score:.4f}",
            str(candidate.unavailable_reason.value if candidate.unavailable_reason else ""),
        )
    console.print(table)


__all__ = [
    "REVIEWABLE_STATUSES",
    "ReviewUpdate",
    "apply_review_update",
    "reviewable_decisions",
    "run_interactive_review",
]
