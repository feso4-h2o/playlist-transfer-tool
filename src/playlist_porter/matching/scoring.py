"""Weighted metadata scoring and match decision assignment."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import EvidenceValue, MatchDecision, TrackCandidate, UniversalTrack
from playlist_porter.normalization import (
    normalize_text_forms,
    normalize_title,
    normalize_title_forms,
)


@dataclass(frozen=True)
class ScoringConfig:
    """Tunable thresholds for metadata matching."""

    duration_tolerance_seconds: int = 3
    duration_mismatch_seconds: int = 5
    duration_max_penalty_seconds: int = 30
    high_confidence_threshold: float = 0.88
    medium_confidence_threshold: float = 0.72
    ambiguity_delta: float = 0.03


_HARD_REVIEW_REASONS = {
    UnavailableReason.DURATION_MISMATCH,
    UnavailableReason.VERSION_MISMATCH,
    UnavailableReason.ARTIST_MISMATCH,
}


def score_candidate(
    source_track: UniversalTrack,
    candidate: TrackCandidate,
    *,
    config: ScoringConfig | None = None,
) -> TrackCandidate:
    """Return a candidate with weighted metadata score and evidence attached."""

    config = config or ScoringConfig()
    destination_track = candidate.track

    title_score = _title_score(source_track, destination_track)
    primary_artist_score = _text_score(
        source_track.primary_artist,
        destination_track.primary_artist,
    )
    all_artist_score = _text_score(
        " ".join(source_track.artists),
        " ".join(destination_track.artists),
    )
    duration_score, duration_delta, duration_reason = _duration_score(
        source_track,
        destination_track,
        config=config,
    )
    version_score, version_reason = _version_score(source_track, destination_track)
    album_score = _optional_text_score(source_track.album, destination_track.album, default=0.85)
    release_score = _release_score(source_track, destination_track)
    explicit_score = _explicit_score(source_track, destination_track)
    rank_score = _rank_score(candidate.rank)

    weighted_score = (
        title_score * 0.27
        + primary_artist_score * 0.21
        + all_artist_score * 0.13
        + duration_score * 0.17
        + version_score * 0.12
        + album_score * 0.04
        + release_score * 0.03
        + explicit_score * 0.01
        + rank_score * 0.02
    )

    reasons = set(_candidate_reasons(candidate))
    if duration_reason is not None:
        reasons.add(duration_reason)
    if version_reason is not None:
        reasons.add(version_reason)
    if primary_artist_score < 0.70 and all_artist_score < 0.70:
        reasons.add(UnavailableReason.ARTIST_MISMATCH)

    evidence: dict[str, EvidenceValue] = {
        **candidate.evidence,
        "title_score": round(title_score, 4),
        "primary_artist_score": round(primary_artist_score, 4),
        "all_artist_score": round(all_artist_score, 4),
        "duration_score": round(duration_score, 4),
        "duration_delta_seconds": duration_delta,
        "version_score": round(version_score, 4),
        "album_score": round(album_score, 4),
        "release_score": round(release_score, 4),
        "explicit_score": round(explicit_score, 4),
        "rank_score": round(rank_score, 4),
        "reason_codes": ",".join(sorted(reason.value for reason in reasons)) or None,
    }

    return candidate.model_copy(
        update={
            "score": round(weighted_score, 4),
            "evidence": evidence,
        }
    )


def rank_candidates(
    source_track: UniversalTrack,
    candidates: list[TrackCandidate],
    *,
    config: ScoringConfig | None = None,
) -> list[TrackCandidate]:
    """Score and sort candidates from most to least likely."""

    scored = [score_candidate(source_track, candidate, config=config) for candidate in candidates]
    scored.sort(
        key=lambda candidate: (
            candidate.score,
            -(candidate.rank),
        ),
        reverse=True,
    )
    return [candidate.model_copy(update={"rank": rank}) for rank, candidate in enumerate(scored, 1)]


def decide_match(
    source_track: UniversalTrack,
    candidates: list[TrackCandidate],
    *,
    config: ScoringConfig | None = None,
) -> MatchDecision:
    """Assign a match status from scored destination candidates."""

    config = config or ScoringConfig()
    ranked = rank_candidates(source_track, candidates, config=config)

    if not ranked:
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus.NOT_FOUND,
            reason_codes=[UnavailableReason.NO_CANDIDATES],
        )

    available = [
        candidate
        for candidate in ranked
        if candidate.unavailable_reason is not UnavailableReason.REGION_UNAVAILABLE
    ]
    if not available:
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus.NOT_FOUND,
            candidates=ranked,
            score=ranked[0].score,
            evidence=ranked[0].evidence,
            reason_codes=[UnavailableReason.REGION_UNAVAILABLE],
        )

    isrc_match = _find_isrc_match(source_track, available)
    if isrc_match is not None:
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus.ISRC_EXACT,
            candidates=ranked,
            selected_candidate=isrc_match.model_copy(
                update={
                    "score": 1.0,
                    "evidence": {**isrc_match.evidence, "isrc_exact": True},
                }
            ),
            score=1.0,
            evidence={**isrc_match.evidence, "isrc_exact": True},
        )

    top = available[0]
    reasons = _candidate_reasons(top)

    if top.score < config.medium_confidence_threshold:
        reason_codes = _dedupe_reasons([UnavailableReason.LOW_CONFIDENCE, *reasons])
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus.NOT_FOUND,
            candidates=ranked,
            score=top.score,
            evidence=top.evidence,
            reason_codes=reason_codes,
        )

    if _is_ambiguous(top, available[1:], config=config):
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus.NEEDS_REVIEW,
            candidates=ranked,
            score=top.score,
            evidence=top.evidence,
            reason_codes=_dedupe_reasons(reasons),
        )

    if reasons & _HARD_REVIEW_REASONS:
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus.NEEDS_REVIEW,
            candidates=ranked,
            score=top.score,
            evidence=top.evidence,
            reason_codes=_dedupe_reasons(reasons),
        )

    if top.score >= config.high_confidence_threshold:
        return MatchDecision(
            source_track=source_track,
            status=MatchStatus.METADATA_HIGH_CONFIDENCE,
            candidates=ranked,
            selected_candidate=top,
            score=top.score,
            evidence=top.evidence,
        )

    return MatchDecision(
        source_track=source_track,
        status=MatchStatus.METADATA_MEDIUM_CONFIDENCE,
        candidates=ranked,
        selected_candidate=top,
        score=top.score,
        evidence=top.evidence,
        reason_codes=_dedupe_reasons(reasons),
    )


def _title_score(source: UniversalTrack, destination: UniversalTrack) -> float:
    source_forms = [title.core for title in normalize_title_forms(source.title)]
    destination_forms = [title.core for title in normalize_title_forms(destination.title)]
    return _best_form_score(source_forms, destination_forms)


def _text_score(left: str, right: str) -> float:
    return _best_form_score(normalize_text_forms(left), normalize_text_forms(right))


def _optional_text_score(left: str | None, right: str | None, *, default: float) -> float:
    if left is None or right is None:
        return default
    return _text_score(left, right)


def _best_form_score(
    left_forms: tuple[str, ...] | list[str],
    right_forms: tuple[str, ...] | list[str],
) -> float:
    return max(
        fuzz.token_set_ratio(left_form, right_form) / 100
        for left_form in left_forms
        for right_form in right_forms
    )


def _duration_score(
    source: UniversalTrack,
    destination: UniversalTrack,
    *,
    config: ScoringConfig,
) -> tuple[float, int | None, UnavailableReason | None]:
    if source.duration_seconds is None or destination.duration_seconds is None:
        return 0.70, None, None

    delta = abs(source.duration_seconds - destination.duration_seconds)
    if delta <= config.duration_tolerance_seconds:
        return 1.0, delta, None

    reason = (
        UnavailableReason.DURATION_MISMATCH
        if delta > config.duration_mismatch_seconds
        else None
    )
    if delta >= config.duration_max_penalty_seconds:
        return 0.0, delta, reason

    penalty_span = config.duration_max_penalty_seconds - config.duration_tolerance_seconds
    score = 1 - ((delta - config.duration_tolerance_seconds) / penalty_span)
    return max(score, 0.0), delta, reason


def _version_score(
    source: UniversalTrack,
    destination: UniversalTrack,
) -> tuple[float, UnavailableReason | None]:
    source_tags = set(normalize_title(source.title).version_tags)
    destination_tags = set(normalize_title(destination.title).version_tags)
    if source_tags == destination_tags:
        return 1.0, None
    if not source_tags and not destination_tags:
        return 1.0, None
    if source_tags & destination_tags:
        return 0.65, None
    return 0.0, UnavailableReason.VERSION_MISMATCH


def _release_score(source: UniversalTrack, destination: UniversalTrack) -> float:
    source_year = source.release_year or (source.release_date.year if source.release_date else None)
    destination_year = destination.release_year or (
        destination.release_date.year if destination.release_date else None
    )
    if source_year is None or destination_year is None:
        return 0.80

    delta = abs(source_year - destination_year)
    if delta == 0:
        return 1.0
    if delta <= 1:
        return 0.90
    if delta <= 3:
        return 0.70
    return 0.40


def _explicit_score(source: UniversalTrack, destination: UniversalTrack) -> float:
    if source.explicit is None or destination.explicit is None:
        return 0.80
    return 1.0 if source.explicit == destination.explicit else 0.60


def _rank_score(rank: int) -> float:
    return max(1 - ((rank - 1) * 0.08), 0.50)


def _candidate_reasons(candidate: TrackCandidate) -> set[UnavailableReason]:
    reasons: set[UnavailableReason] = set()
    if candidate.unavailable_reason is not None:
        reasons.add(candidate.unavailable_reason)
    evidence_reasons = candidate.evidence.get("reason_codes")
    if isinstance(evidence_reasons, str):
        reasons.update(
            UnavailableReason(reason)
            for reason in evidence_reasons.split(",")
            if reason
        )
    return reasons


def _find_isrc_match(
    source_track: UniversalTrack,
    candidates: list[TrackCandidate],
) -> TrackCandidate | None:
    if source_track.isrc is None:
        return None
    source_isrc = source_track.isrc.casefold()
    for candidate in candidates:
        if candidate.track.isrc and candidate.track.isrc.casefold() == source_isrc:
            return candidate
    return None


def _is_ambiguous(
    top: TrackCandidate,
    rest: list[TrackCandidate],
    *,
    config: ScoringConfig,
) -> bool:
    if not rest:
        return False
    second = rest[0]
    return (
        second.score >= config.medium_confidence_threshold
        and (top.score - second.score) <= config.ambiguity_delta
    )


def _dedupe_reasons(
    reasons: list[UnavailableReason] | set[UnavailableReason],
) -> list[UnavailableReason]:
    return [reason for reason in UnavailableReason if reason in reasons]


__all__ = ["ScoringConfig", "decide_match", "rank_candidates", "score_candidate"]
