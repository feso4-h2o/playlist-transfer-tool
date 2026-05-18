"""Reusable progress event helpers for workflow callers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

ProgressPhase = Literal["match", "write"]


@dataclass(frozen=True)
class ProgressEvent:
    """One progress update emitted by workflow code."""

    phase: ProgressPhase
    current: int
    total: int
    label: str | None = None


ProgressReporter = Callable[[ProgressEvent], None]


def report_progress(
    reporter: ProgressReporter | None,
    *,
    phase: ProgressPhase,
    current: int,
    total: int,
    label: str | None = None,
) -> None:
    """Emit a progress event when a caller supplied a reporter."""

    if reporter is None:
        return
    reporter(ProgressEvent(phase=phase, current=current, total=total, label=label))


__all__ = ["ProgressEvent", "ProgressPhase", "ProgressReporter", "report_progress"]
