from __future__ import annotations

from playlist_porter.cli import build_parser
from playlist_porter.matching.status import MatchStatus, UnavailableReason
from playlist_porter.models import MatchDecision, TrackCandidate, TransferRun, UniversalTrack
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.review.terminal import (
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
        args = parser.parse_args(["review", "--action", action])
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
            "Action [accept/reject/skip] or [a/r/s]",
            {
                "choices": ["accept", "reject", "skip", "a", "r", "s"],
                "default": "skip",
                "show_choices": False,
                "console": prompt_calls[0][1]["console"],
            },
        )
    ]
