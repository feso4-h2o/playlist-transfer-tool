from __future__ import annotations

from io import StringIO

from rich.console import Console

from playlist_porter.cli import build_parser
from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import MatchDecision, TrackCandidate, TransferRun, UniversalTrack
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.review.terminal import (
    _ACTION_PROMPT,
    _ACTION_PROMPT_WITH_OVERRIDE,
    _ACTION_PROMPT_WITHOUT_OVERRIDE,
    ReviewUpdate,
    apply_review_update,
    run_interactive_review,
)


def _track(title: str, track_id: str) -> UniversalTrack:
    return UniversalTrack(
        title=title,
        artists=["Artist"],
        platform="mock",
        platform_track_id=track_id,
        duration_seconds=180,
    )


def _repository_with_review_decision(tmp_path) -> tuple[TransferRepository, str, UniversalTrack]:
    repository = TransferRepository(tmp_path / "transfer.sqlite")
    run_id = repository.create_run(
        TransferRun(source_platform="mock", destination_platform="mock")
    )
    source = _track("Source", "source-1")
    candidate = TrackCandidate(
        track=_track("Destination", "dest-1"),
        score=0.91,
        rank=1,
    )
    repository.save_match_decision(
        run_id,
        MatchDecision(
            source_track=source,
            status=MatchStatus.NEEDS_REVIEW,
            candidates=[candidate],
            score=candidate.score,
            reason_codes=[UnavailableReason.AMBIGUOUS_CANDIDATES],
        ),
    )
    return repository, run_id, source


def test_review_cli_accepts_action_aliases() -> None:
    parser = build_parser()

    for action in ("a", "r", "s"):
        args = parser.parse_args(["review", "--config", "porter.json", "--action", action])
        assert args.action == action


def test_review_action_alias_accepts_candidate(tmp_path) -> None:
    repository, run_id, source = _repository_with_review_decision(tmp_path)

    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="a"),
    )

    override = repository.load_user_override(run_id, source.internal_id)
    assert override is not None
    assert override.status is MatchStatus.USER_APPROVED


def test_review_action_alias_rejects_candidate(tmp_path) -> None:
    repository, run_id, source = _repository_with_review_decision(tmp_path)

    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="r"),
    )

    override = repository.load_user_override(run_id, source.internal_id)
    assert override is not None
    assert override.status is MatchStatus.USER_REJECTED


def test_review_action_alias_skips_without_saving_override(tmp_path) -> None:
    repository, run_id, source = _repository_with_review_decision(tmp_path)

    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="s"),
    )

    assert repository.load_user_override(run_id, source.internal_id) is None


def test_interactive_review_prompt_separates_long_actions_from_aliases(
    tmp_path,
    monkeypatch,
) -> None:
    repository, run_id, _ = _repository_with_review_decision(tmp_path)
    prompt_calls = []

    def ask(prompt, **kwargs):
        prompt_calls.append((prompt, kwargs))
        return "s"

    monkeypatch.setattr("playlist_porter.review.terminal.Prompt.ask", ask)

    saved_count = run_interactive_review(repository, run_id)

    assert saved_count == 0
    assert prompt_calls == [
        (
            _ACTION_PROMPT_WITHOUT_OVERRIDE,
            {
                "choices": ["accept", "reject", "skip", "a", "r", "s"],
                "default": "skip",
                "show_choices": False,
                "console": prompt_calls[0][1]["console"],
            },
        )
    ]


def test_interactive_review_shows_current_review_position(tmp_path, monkeypatch) -> None:
    repository, run_id, _ = _repository_with_review_decision(tmp_path)

    def ask(prompt, **kwargs):
        del prompt, kwargs
        return "s"

    output = StringIO()
    console = Console(file=output, force_terminal=False)
    monkeypatch.setattr("playlist_porter.review.terminal.Prompt.ask", ask)

    saved_count = run_interactive_review(repository, run_id, console=console)
    lines = output.getvalue().splitlines()

    assert saved_count == 0
    assert any(
        line.startswith("(Review 1/1) |") and "Current decision: none" in line
        for line in lines
    )
    assert not any(line.strip() == "Review 1/1" for line in lines)


def test_interactive_review_can_hide_current_review_position(tmp_path, monkeypatch) -> None:
    repository, run_id, _ = _repository_with_review_decision(tmp_path)

    def ask(prompt, **kwargs):
        del prompt, kwargs
        return "s"

    output = StringIO()
    console = Console(file=output, force_terminal=False)
    monkeypatch.setattr("playlist_porter.review.terminal.Prompt.ask", ask)

    saved_count = run_interactive_review(
        repository,
        run_id,
        console=console,
        show_position=False,
    )

    assert saved_count == 0
    assert "Review 1/1" not in output.getvalue()


def test_interactive_review_prompt_shows_existing_decision_and_skip_keeps_it(
    tmp_path,
    monkeypatch,
) -> None:
    repository, run_id, source = _repository_with_review_decision(tmp_path)
    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="a"),
    )
    prompt_calls = []

    def ask(prompt, **kwargs):
        prompt_calls.append((prompt, kwargs))
        return "s"

    monkeypatch.setattr("playlist_porter.review.terminal.Prompt.ask", ask)

    saved_count = run_interactive_review(repository, run_id)

    override = repository.load_user_override(run_id, source.internal_id)
    assert saved_count == 0
    assert override is not None
    assert override.status is MatchStatus.USER_APPROVED
    assert prompt_calls == [
        (
            _ACTION_PROMPT_WITH_OVERRIDE,
            {
                "choices": ["accept", "reject", "skip", "a", "r", "s"],
                "default": "skip",
                "show_choices": False,
                "console": prompt_calls[0][1]["console"],
            },
        )
    ]


def test_interactive_review_pending_only_hides_existing_decisions(
    tmp_path,
    monkeypatch,
) -> None:
    repository, run_id, source = _repository_with_review_decision(tmp_path)
    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="a"),
    )
    prompt_calls = []

    def ask(prompt, **kwargs):
        prompt_calls.append((prompt, kwargs))
        return "s"

    output = StringIO()
    console = Console(file=output, force_terminal=False)
    monkeypatch.setattr("playlist_porter.review.terminal.Prompt.ask", ask)

    saved_count = run_interactive_review(
        repository,
        run_id,
        console=console,
        pending_only=True,
    )

    assert saved_count == 0
    assert prompt_calls == []
    assert output.getvalue().strip() == "No pending tracks to review."


def test_interactive_review_reports_when_nothing_is_reviewable(tmp_path, monkeypatch) -> None:
    repository = TransferRepository(tmp_path / "transfer.sqlite")
    run_id = repository.create_run(
        TransferRun(source_platform="mock", destination_platform="mock")
    )
    source = _track("Source", "source-1")
    candidate = TrackCandidate(
        track=_track("Destination", "dest-1"),
        score=1.0,
        rank=1,
    )
    repository.save_match_decision(
        run_id,
        MatchDecision(
            source_track=source,
            status=MatchStatus.METADATA_HIGH_CONFIDENCE,
            candidates=[candidate],
            selected_candidate=candidate,
            score=candidate.score,
        ),
    )
    prompt_calls = []

    def ask(prompt, **kwargs):
        prompt_calls.append((prompt, kwargs))
        return "s"

    output = StringIO()
    console = Console(file=output, force_terminal=False)
    monkeypatch.setattr("playlist_porter.review.terminal.Prompt.ask", ask)

    saved_count = run_interactive_review(repository, run_id, console=console)

    assert saved_count == 0
    assert prompt_calls == []
    assert output.getvalue().strip() == "No tracks to review."


def test_interactive_review_pending_only_keeps_unresolved_decisions(
    tmp_path,
    monkeypatch,
) -> None:
    repository, run_id, _ = _repository_with_review_decision(tmp_path)
    prompt_calls = []

    def ask(prompt, **kwargs):
        prompt_calls.append((prompt, kwargs))
        return "s"

    monkeypatch.setattr("playlist_porter.review.terminal.Prompt.ask", ask)

    saved_count = run_interactive_review(repository, run_id, pending_only=True)

    assert saved_count == 0
    assert [prompt for prompt, _ in prompt_calls] == [_ACTION_PROMPT_WITHOUT_OVERRIDE]


def test_review_update_debug_logs_overwritten_override(tmp_path, monkeypatch) -> None:
    repository, run_id, source = _repository_with_review_decision(tmp_path)
    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="a"),
    )
    debug_records = []

    class DebugLogger:
        def debug(self, message, **kwargs):
            debug_records.append((message, kwargs))

    monkeypatch.setattr("playlist_porter.review.terminal.REVIEW_DIAGNOSTICS", DebugLogger())

    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="r"),
    )

    requested = next(record for record in debug_records if record[0] == "review update requested")
    rejected = next(record for record in debug_records if record[0] == "review candidate rejected")
    assert requested[1]["existing_override"]["status"] == "user_approved"
    assert rejected[1]["overwrites_existing"] is True
    assert rejected[1]["existing_override"]["status"] == "user_approved"


def test_review_skip_debug_logs_no_persistence_change(tmp_path, monkeypatch) -> None:
    repository, run_id, source = _repository_with_review_decision(tmp_path)
    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="a"),
    )
    debug_records = []

    class DebugLogger:
        def debug(self, message, **kwargs):
            debug_records.append((message, kwargs))

    monkeypatch.setattr("playlist_porter.review.terminal.REVIEW_DIAGNOSTICS", DebugLogger())

    apply_review_update(
        repository,
        run_id,
        ReviewUpdate(source_track_internal_id=str(source.internal_id), action="s"),
    )

    skipped = next(record for record in debug_records if record[0] == "review update skipped")
    override = repository.load_user_override(run_id, source.internal_id)
    assert skipped[1]["persistence_changed"] is False
    assert skipped[1]["existing_override_kept"] is True
    assert skipped[1]["existing_override"]["status"] == "user_approved"
    assert override is not None
    assert override.status is MatchStatus.USER_APPROVED


def test_interactive_review_prompt_renders_literal_brackets() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)

    console.print(_ACTION_PROMPT)

    assert output.getvalue().strip() == "Action [accept/reject/skip] or [a/r/s]"


def test_interactive_review_dynamic_prompts_render_literal_brackets() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)

    console.print(_ACTION_PROMPT_WITH_OVERRIDE)
    console.print(_ACTION_PROMPT_WITHOUT_OVERRIDE)

    assert output.getvalue().splitlines() == [
        "Action [accept/reject/skip] or [a/r/s] (skip keeps current decision)",
        "Action [accept/reject/skip] or [a/r/s] (skip leaves unresolved)",
    ]
