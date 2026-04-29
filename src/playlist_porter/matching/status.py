"""Match and unavailability status values used across the transfer flow."""

from enum import StrEnum


class MatchStatus(StrEnum):
    """Known match decision states.

    A title/artist fingerprint is deliberately not represented here as an exact
    status because it is only a grouping/cache key, not evidence of identity.
    """

    ISRC_EXACT = "isrc_exact"
    METADATA_HIGH_CONFIDENCE = "metadata_high_confidence"
    METADATA_MEDIUM_CONFIDENCE = "metadata_medium_confidence"
    NEEDS_REVIEW = "needs_review"
    NOT_FOUND = "not_found"
    USER_APPROVED = "user_approved"
    USER_REJECTED = "user_rejected"


class UnavailableReason(StrEnum):
    """Reason codes for unresolved or unavailable destination tracks."""

    NO_CANDIDATES = "no_candidates"
    LOW_CONFIDENCE = "low_confidence"
    DURATION_MISMATCH = "duration_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    ARTIST_MISMATCH = "artist_mismatch"
    REGION_UNAVAILABLE = "region_unavailable"
