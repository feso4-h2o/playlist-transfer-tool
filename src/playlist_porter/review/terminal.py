"""Terminal review helpers for persisted match decisions."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

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

_ACTION_ALIASES = {
    "a": "accept",
    "accept": "accept",
    "r": "reject",
    "reject": "reject",
    "s": "skip",
    "skip": "skip",
}
_ACTION_PROMPT = r"Action \[accept/reject/skip] or \[a/r/s]"


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
    action = _normalize_review_action(update.action)
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


def _normalize_review_action(action: str) -> str:
    normalized = _ACTION_ALIASES.get(action.strip().casefold())
    if normalized is None:
        raise ValueError(f"unknown review action: {action}")
    return normalized


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
            _ACTION_PROMPT,
            choices=["accept", "reject", "skip", "a", "r", "s"],
            default="skip",
            show_choices=False,
            console=console,
        )
        action = _normalize_review_action(action)
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
    console.print(f"\n[bold]{escape(source.title)}[/bold] - {escape(', '.join(source.artists))}")
    console.print(
        f"status={decision.status.value} score={_decision_score_text(decision.score)} "
        f"reasons={','.join(reason.value for reason in decision.reason_codes) or '-'}"
    )
    console.print(_source_metadata(source))
    table = Table("Rank", "Track", "Score", "Metadata", "IDs", "Reasons", show_lines=True)
    for candidate in decision.candidates:
        table.add_row(
            str(candidate.rank),
            _candidate_identity(candidate),
            _score_text(candidate),
            _candidate_metadata(candidate),
            _candidate_ids(candidate),
            _candidate_reason_text(candidate),
        )
    console.print(table)


def _candidate_identity(candidate: TrackCandidate) -> str:
    track = candidate.track
    return f"{escape(track.title)}\n{escape(', '.join(track.artists))}"


def _decision_score_text(score: float | None) -> str:
    return f"{score:.4f}" if score is not None else "-"


def _score_text(candidate: TrackCandidate) -> str:
    return f"{candidate.score:.4f}"


def _source_metadata(track: UniversalTrack) -> str | Text:
    metadata = _track_metadata_fields(track, include_album=True)
    ids = _track_id_fields(track)
    output = Text()
    for block in (metadata, ids):
        _append_block(output, block)
    position = _position_text(track.source_playlist_position)
    if position is not None:
        _append_block(output, position)
    return output if output.plain else "-"


def _candidate_metadata(candidate: TrackCandidate) -> str | Text:
    return _track_metadata_fields(candidate.track, include_album=True)


def _candidate_ids(candidate: TrackCandidate) -> str | Text:
    track = candidate.track
    return _track_id_fields(track, url=_candidate_destination_url(candidate))


def _candidate_reason_text(candidate: TrackCandidate) -> str:
    reasons: list[str] = []
    if candidate.unavailable_reason is not None:
        reasons.append(candidate.unavailable_reason.value)
    evidence_reasons = candidate.evidence.get("reason_codes")
    if isinstance(evidence_reasons, str):
        reasons.extend(reason for reason in evidence_reasons.split(",") if reason)
    return escape(", ".join(dict.fromkeys(reasons))) or "-"


def _track_metadata_fields(track: UniversalTrack, *, include_album: bool) -> str | Text:
    values = [
        ("Album", track.album if include_album else None),
        ("Duration", _duration_text(track.duration_seconds)),
        ("Release", _release_text(track)),
        ("Explicit", _explicit_text(track.explicit)),
    ]
    return _joined_fields(values)


def _track_id_fields(track: UniversalTrack, *, url: str | None = None) -> str | Text:
    destination_url = url or _destination_url(track.platform, track.platform_track_id)
    return _joined_fields(
        [
            ("ISRC", track.isrc),
            ("Platform ID", track.platform_track_id),
            ("URL", _destination_link(destination_url)),
        ]
    )


def _joined_fields(values: list[tuple[str, str | Text | None]]) -> str | Text:
    output = Text()
    for name, value in values:
        if value is None or value == "":
            continue
        if output.plain:
            output.append("\n")
        output.append(f"{name}: ")
        if isinstance(value, Text):
            output.append_text(value)
        else:
            output.append(str(value))
    return output if output.plain else "-"


def _append_block(output: Text, block: str | Text) -> None:
    if block == "-":
        return
    if output.plain:
        output.append("\n")
    if isinstance(block, Text):
        output.append_text(block)
    else:
        output.append(block)


def _position_text(position: int | None) -> str | None:
    if position is None:
        return None
    return f"Position: {position}"


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


def _candidate_destination_url(candidate: TrackCandidate) -> str | None:
    if candidate.track.platform is None or candidate.track.platform.casefold() != "qqmusic":
        return _destination_url(candidate.track.platform, candidate.track.platform_track_id)
    evidence_url = _optional_text(candidate.evidence.get("qqmusic_url"))
    if evidence_url is not None:
        return evidence_url
    songmid = _optional_text(candidate.evidence.get("qqmusic_songmid"))
    if songmid is not None:
        return f"https://y.qq.com/n/ryqq/songDetail/{songmid}"
    return _destination_url(candidate.track.platform, candidate.track.platform_track_id)


def _is_qqmusic_songmid(platform_track_id: str) -> bool:
    return ":" not in platform_track_id and not platform_track_id.isdigit()


def _destination_link(url: str | None) -> Text | None:
    if url is None:
        return None
    return Text("Link", style=f"link {url}")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "REVIEWABLE_STATUSES",
    "ReviewUpdate",
    "apply_review_update",
    "reviewable_decisions",
    "run_interactive_review",
]
