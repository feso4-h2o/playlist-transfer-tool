"""Metadata normalization and deterministic candidate-key helpers."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from opencc import OpenCC

_SIMPLIFIED_CONVERTER = OpenCC("t2s")
_TRADITIONAL_CONVERTER = OpenCC("s2t")

_BRACKETED_TEXT_RE = re.compile(
    "\\((?P<round>[^)]*)\\)|\\[(?P<square>[^\\]]*)\\]|\\{(?P<brace>[^}]*)\\}|"
    "\\uFF08(?P<cjk_round>[^\\uFF09]*)\\uFF09|"
    "\\u3010(?P<cjk_square>[^\\u3011]*)\\u3011"
)
_SEPARATOR_RE = re.compile("[\\s\\-_/\\\\|:;,.!?'\"`~+=*#@&()[\\]{}\\uFF08\\uFF09\\u3010\\u3011]+")
_WHITESPACE_RE = re.compile(r"\s+")
_VERSION_PATTERNS: dict[str, re.Pattern[str]] = {
    "live": re.compile(r"\blive\b", re.IGNORECASE),
    "remix": re.compile(r"\bremix(?:ed)?\b", re.IGNORECASE),
    "acoustic": re.compile(r"\bacoustic\b", re.IGNORECASE),
    "remaster": re.compile(r"\bremaster(?:ed)?\b", re.IGNORECASE),
    "instrumental": re.compile(r"\binstrumental\b", re.IGNORECASE),
    "karaoke": re.compile(r"\bkaraoke\b", re.IGNORECASE),
    "demo": re.compile(r"\bdemo\b", re.IGNORECASE),
    "radio_edit": re.compile(r"\bradio\s+edit\b", re.IGNORECASE),
    "clean": re.compile(r"\bclean\b", re.IGNORECASE),
    "explicit": re.compile(r"\bexplicit\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class NormalizedTitle:
    """Normalized title forms used by search and scoring."""

    full: str
    core: str
    version_tags: tuple[str, ...]


def normalize_whitespace(value: str) -> str:
    """Trim and collapse internal whitespace."""

    return _WHITESPACE_RE.sub(" ", value).strip()


def normalize_punctuation(value: str) -> str:
    """Normalize Unicode width and collapse punctuation/separators to spaces."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _SEPARATOR_RE.sub(" ", normalized)
    return normalize_whitespace(normalized)


def normalize_text(value: str, *, target_script: str | None = None) -> str:
    """Normalize free text for matching.

    ``target_script`` may be ``"simplified"`` or ``"traditional"`` to produce
    query forms for both major Chinese script variants.
    """

    normalized = normalize_punctuation(value).casefold()
    if target_script == "simplified":
        normalized = _SIMPLIFIED_CONVERTER.convert(normalized)
    elif target_script == "traditional":
        normalized = _TRADITIONAL_CONVERTER.convert(normalized)
    elif target_script is not None:
        raise ValueError("target_script must be 'simplified', 'traditional', or None")
    return normalize_whitespace(normalized)


def normalize_text_forms(value: str) -> tuple[str, ...]:
    """Return unique default, simplified-target, and traditional-target forms."""

    forms = (
        normalize_text(value),
        normalize_text(value, target_script="simplified"),
        normalize_text(value, target_script="traditional"),
    )
    return tuple(dict.fromkeys(form for form in forms if form))


def extract_version_tags(value: str) -> tuple[str, ...]:
    """Return canonical version tags found in title or bracketed descriptors."""

    normalized = normalize_punctuation(value)
    tags = [tag for tag, pattern in _VERSION_PATTERNS.items() if pattern.search(normalized)]
    return tuple(tags)


def normalize_title(value: str, *, target_script: str | None = None) -> NormalizedTitle:
    """Normalize a title while removing bracketed descriptors from the core title."""

    version_tags: list[str] = []

    def replace_bracketed(match: re.Match[str]) -> str:
        bracket_text = next(group for group in match.groups() if group is not None)
        version_tags.extend(extract_version_tags(bracket_text))
        return " "

    without_brackets = _BRACKETED_TEXT_RE.sub(replace_bracketed, value)
    version_tags.extend(extract_version_tags(value))

    full = normalize_text(value, target_script=target_script)
    core = normalize_text(without_brackets, target_script=target_script)
    return NormalizedTitle(
        full=full,
        core=core,
        version_tags=tuple(dict.fromkeys(version_tags)),
    )


def normalize_title_forms(value: str) -> tuple[NormalizedTitle, ...]:
    """Return normalized title forms for default, simplified, and traditional search."""

    forms = (
        normalize_title(value),
        normalize_title(value, target_script="simplified"),
        normalize_title(value, target_script="traditional"),
    )
    unique: dict[tuple[str, str, tuple[str, ...]], NormalizedTitle] = {}
    for form in forms:
        unique[(form.full, form.core, form.version_tags)] = form
    return tuple(unique.values())


def track_fingerprint(title: str, primary_artist: str) -> str:
    """Return a deterministic candidate grouping key.

    This hash is based only on normalized title and primary artist metadata. It
    must never be used as proof that two platform tracks are the same recording.
    """

    normalized_title = normalize_title(title, target_script="simplified").core
    normalized_artist = normalize_text(primary_artist, target_script="simplified")
    payload = f"{normalized_title}\0{normalized_artist}".encode()
    return hashlib.blake2b(payload, digest_size=32).hexdigest()
