from playlist_porter.normalization import (
    normalize_text,
    normalize_text_forms,
    normalize_title,
    normalize_title_forms,
    track_fingerprint,
)


def test_normalize_text_trims_casefolds_and_collapses_separators() -> None:
    assert normalize_text("  Hello---WORLD / Test!!!  ") == "hello world test"


def test_normalize_title_strips_brackets_but_preserves_version_tags() -> None:
    normalized = normalize_title("Song Title (Live Remix)")

    assert normalized.full == "song title live remix"
    assert normalized.core == "song title"
    assert normalized.version_tags == ("live", "remix")


def test_normalize_text_forms_include_simplified_and_traditional_chinese() -> None:
    forms = normalize_text_forms("後來")

    assert "后来" in forms
    assert "後來" in forms


def test_normalize_title_forms_include_script_variants() -> None:
    forms = normalize_title_forms("後來 (Live)")
    cores = {form.core for form in forms}

    assert "后来" in cores
    assert "後來" in cores
    assert all("live" in form.version_tags for form in forms)


def test_normalize_title_strips_cjk_bracket_styles() -> None:
    normalized = normalize_title("\u5f8c\u4f86\u3010Remix\u3011", target_script="simplified")

    assert normalized.core == "\u540e\u6765"
    assert normalized.version_tags == ("remix",)


def test_track_fingerprint_is_deterministic_after_normalization() -> None:
    first = track_fingerprint("  Hello---World (Live) ", "The Artist")
    second = track_fingerprint("hello world", "the artist")

    assert first == second
    assert len(first) == 64


def test_track_fingerprint_changes_for_different_primary_artist() -> None:
    assert track_fingerprint("Hello World", "Artist A") != track_fingerprint(
        "Hello World",
        "Artist B",
    )
