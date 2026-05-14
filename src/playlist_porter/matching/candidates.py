"""Candidate query generation and local matching orchestration."""

from __future__ import annotations

from loguru import logger

from playlist_porter.matching.scoring import ScoringConfig, decide_match
from playlist_porter.models import MatchDecision, Playlist, TrackCandidate, UniversalTrack
from playlist_porter.normalization import (
    normalize_text,
    normalize_text_forms,
    normalize_title_forms,
)
from playlist_porter.platforms.base import BasePlatform


def build_search_queries(track: UniversalTrack) -> tuple[str, ...]:
    """Build broadened metadata search queries for one source track."""

    queries: list[str] = []
    artist_forms = normalize_text_forms(track.primary_artist)
    all_artist_forms = normalize_text_forms(" ".join(track.artists))

    for title_form in normalize_title_forms(track.title):
        title_values = [title_form.core, title_form.full]
        if title_form.version_tags:
            title_values.append(f"{title_form.core} {' '.join(title_form.version_tags)}")

        for title_value in title_values:
            for artist_value in (*artist_forms, *all_artist_forms):
                queries.append(normalize_text(f"{title_value} {artist_value}"))

    return tuple(dict.fromkeys(query for query in queries if query))


def generate_candidates(
    source_track: UniversalTrack,
    destination: BasePlatform,
    *,
    limit: int = 5,
    per_query_limit: int = 10,
) -> list[TrackCandidate]:
    """Search a destination adapter with broadened queries and de-duplicate results."""

    by_identity: dict[str, TrackCandidate] = {}
    queries = build_search_queries(source_track)
    logger.debug(
        "generated search queries",
        query_count=len(queries),
        destination_platform=destination.platform_name,
    )

    search_count = 0
    for query in queries:
        search_count += 1
        for candidate in destination.search_tracks(query, limit=per_query_limit):
            identity = candidate.track.platform_track_id or str(candidate.track.internal_id)
            existing = by_identity.get(identity)
            if existing is None or candidate.score > existing.score:
                by_identity[identity] = candidate

    candidates = sorted(
        by_identity.values(),
        key=lambda candidate: (candidate.score, -candidate.rank),
        reverse=True,
    )
    result = [
        candidate.model_copy(update={"rank": rank})
        for rank, candidate in enumerate(candidates[:limit], start=1)
    ]
    logger.debug(
        "destination search candidates generated",
        search_count=search_count,
        unique_candidate_count=len(by_identity),
        returned_candidate_count=len(result),
        destination_platform=destination.platform_name,
    )
    return result


def match_track(
    source_track: UniversalTrack,
    destination: BasePlatform,
    *,
    candidate_limit: int = 5,
    config: ScoringConfig | None = None,
) -> MatchDecision:
    """Generate candidates and assign one match decision for a source track."""

    candidates = generate_candidates(source_track, destination, limit=candidate_limit)
    return decide_match(source_track, candidates, config=config)


def match_playlist(
    source_playlist: Playlist,
    destination: BasePlatform,
    *,
    candidate_limit: int = 5,
    config: ScoringConfig | None = None,
) -> list[MatchDecision]:
    """Match all source playlist tracks against a destination adapter."""

    logger.info(
        "playlist matching started",
        source_platform=source_playlist.platform,
        destination_platform=destination.platform_name,
        track_count=len(source_playlist.tracks),
    )
    decisions = [
        match_track(track, destination, candidate_limit=candidate_limit, config=config)
        for track in source_playlist.tracks
    ]
    logger.info(
        "playlist matching finished",
        source_platform=source_playlist.platform,
        destination_platform=destination.platform_name,
        track_count=len(source_playlist.tracks),
        decision_count=len(decisions),
        candidate_count=sum(len(decision.candidates) for decision in decisions),
    )
    return decisions


__all__ = ["build_search_queries", "generate_candidates", "match_playlist", "match_track"]
