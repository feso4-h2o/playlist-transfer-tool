"""Adapter-level rate-limit, retry, and circuit-breaker policies."""

from __future__ import annotations

import random as random_module
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, TypeVar

T = TypeVar("T")

Clock = Callable[[], float]
Sleeper = Callable[[float], None]
RandomSource = Callable[[], float]
RequestKind = Literal["read", "write"]


class RetryCategory(StrEnum):
    """Retry classification used by platform adapters."""

    TRANSIENT_NETWORK = "transient_network"
    THROTTLED = "throttled"
    TEMPORARY_SERVER = "temporary_server"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"


class RetryPolicyError(RuntimeError):
    """Base error carrying a retry category."""

    category: RetryCategory

    def __init__(self, message: str, *, category: RetryCategory) -> None:
        super().__init__(message)
        self.category = category


class TransientNetworkError(RetryPolicyError):
    """Temporary network failure that can be retried."""

    def __init__(self, message: str = "transient network failure") -> None:
        super().__init__(message, category=RetryCategory.TRANSIENT_NETWORK)


class TemporaryServerError(RetryPolicyError):
    """Temporary 5xx-style destination-platform failure."""

    def __init__(self, message: str = "temporary server failure") -> None:
        super().__init__(message, category=RetryCategory.TEMPORARY_SERVER)


class RateLimitExceeded(RetryPolicyError):
    """HTTP 429 or suspected throttling signal."""

    def __init__(
        self,
        message: str = "rate limit exceeded",
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, category=RetryCategory.THROTTLED)
        self.retry_after_seconds = retry_after_seconds


class AuthenticationFailure(RetryPolicyError):
    """Non-retryable authentication, cookie, or session failure."""

    def __init__(self, message: str = "authentication failed") -> None:
        super().__init__(message, category=RetryCategory.AUTHENTICATION)


class ValidationFailure(RetryPolicyError):
    """Non-retryable validation or malformed request failure."""

    def __init__(self, message: str = "validation failed") -> None:
        super().__init__(message, category=RetryCategory.VALIDATION)


class RetryBudgetExceeded(RuntimeError):
    """Raised when a retryable operation exhausts its retry budget."""


class CircuitBreakerOpen(RuntimeError):
    """Raised when repeated QQ Music failures should pause transfer work."""

    def __init__(self, message: str, *, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class BackoffConfig:
    """Capped exponential backoff settings."""

    max_attempts: int = 4
    initial_seconds: float = 1.0
    multiplier: float = 2.0
    max_seconds: float = 30.0
    jitter_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_seconds < 0:
            raise ValueError("initial_seconds must be non-negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.max_seconds < 0:
            raise ValueError("max_seconds must be non-negative")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds must be non-negative")


@dataclass
class RollingWindowLimiter:
    """Local rolling-window limiter for platform request caps."""

    max_requests: int
    window_seconds: float = 30.0
    clock: Clock = time.monotonic
    sleep: Sleeper = time.sleep
    _request_times: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

    def wait_for_slot(self) -> None:
        """Wait until the rolling window has capacity, then reserve a slot."""

        now = self.clock()
        self._prune(now)
        if len(self._request_times) >= self.max_requests:
            wait_seconds = self.window_seconds - (now - self._request_times[0])
            if wait_seconds > 0:
                self.sleep(wait_seconds)
                now = self.clock()
                self._prune(now)
        self._request_times.append(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._request_times = [timestamp for timestamp in self._request_times if timestamp > cutoff]


@dataclass
class RequestPacer:
    """Separate proactive pacing for QQ Music reads and writes."""

    read_interval_seconds: float = 0.5
    write_interval_seconds: float = 1.0
    clock: Clock = time.monotonic
    sleep: Sleeper = time.sleep
    _last_started_at: dict[RequestKind, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.read_interval_seconds < 0:
            raise ValueError("read_interval_seconds must be non-negative")
        if self.write_interval_seconds < 0:
            raise ValueError("write_interval_seconds must be non-negative")

    def wait(self, request_kind: RequestKind) -> None:
        """Pace request starts independently for read and write operations."""

        now = self.clock()
        last_started_at = self._last_started_at.get(request_kind)
        interval = (
            self.read_interval_seconds
            if request_kind == "read"
            else self.write_interval_seconds
        )
        if last_started_at is not None:
            wait_seconds = interval - (now - last_started_at)
            if wait_seconds > 0:
                self.sleep(wait_seconds)
                now = self.clock()
        self._last_started_at[request_kind] = now


class SpotifyRateLimitPolicy:
    """Spotify retry policy with rolling-window local limiting."""

    def __init__(
        self,
        *,
        limiter: RollingWindowLimiter | None = None,
        backoff: BackoffConfig | None = None,
        sleep: Sleeper = time.sleep,
        random: RandomSource = random_module.random,
    ) -> None:
        self.limiter = limiter
        self.backoff = backoff or BackoffConfig(max_attempts=4, max_seconds=30.0)
        self.sleep = sleep
        self.random = random

    def execute(self, operation_name: str, operation: Callable[[], T]) -> T:
        """Run an adapter operation under Spotify retry behavior."""

        for attempt in range(1, self.backoff.max_attempts + 1):
            if self.limiter is not None:
                self.limiter.wait_for_slot()
            try:
                return operation()
            except RetryPolicyError as exc:
                if not _is_retryable(exc.category):
                    raise
                if attempt >= self.backoff.max_attempts:
                    raise RetryBudgetExceeded(
                        f"{operation_name} exhausted retry budget"
                    ) from exc
                self.sleep(self._delay_for(exc, retry_index=attempt))
        raise AssertionError("retry loop exited unexpectedly")

    def wrap(self, operation_name: str, operation: Callable[..., T]) -> Callable[..., T]:
        """Return an adapter-method wrapper that applies this policy."""

        def wrapped(*args: object, **kwargs: object) -> T:
            return self.execute(operation_name, lambda: operation(*args, **kwargs))

        return wrapped

    def _delay_for(self, exc: RetryPolicyError, *, retry_index: int) -> float:
        retry_after = getattr(exc, "retry_after_seconds", None)
        if isinstance(retry_after, int | float) and retry_after >= 0:
            return retry_after + self._bounded_jitter()
        return _capped_exponential_delay(self.backoff, retry_index) + self._bounded_jitter()

    def _bounded_jitter(self) -> float:
        return self.random() * self.backoff.jitter_seconds


class QQMusicRateLimitPolicy:
    """Conservative QQ Music retry policy with pacing and circuit breaking."""

    def __init__(
        self,
        *,
        pacer: RequestPacer | None = None,
        backoff: BackoffConfig | None = None,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 60.0,
        clock: Clock = time.monotonic,
        sleep: Sleeper = time.sleep,
        random: RandomSource = random_module.random,
    ) -> None:
        if circuit_breaker_threshold < 1:
            raise ValueError("circuit_breaker_threshold must be at least 1")
        if circuit_breaker_cooldown_seconds < 0:
            raise ValueError("circuit_breaker_cooldown_seconds must be non-negative")
        self.pacer = pacer or RequestPacer(clock=clock, sleep=sleep)
        self.backoff = backoff or BackoffConfig(
            max_attempts=4,
            max_seconds=30.0,
            jitter_seconds=0.0,
        )
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_cooldown_seconds = circuit_breaker_cooldown_seconds
        self.clock = clock
        self.sleep = sleep
        self.random = random
        self._consecutive_failures = 0
        self._circuit_opened_at: float | None = None

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive retryable failure count."""

        return self._consecutive_failures

    def execute(
        self,
        operation_name: str,
        operation: Callable[[], T],
        *,
        request_kind: RequestKind = "read",
    ) -> T:
        """Run an adapter operation under QQ Music retry and pacing behavior."""

        self._raise_if_circuit_open(operation_name)
        for attempt in range(1, self.backoff.max_attempts + 1):
            self.pacer.wait(request_kind)
            try:
                result = operation()
            except RetryPolicyError as exc:
                if not _is_retryable(exc.category):
                    raise
                self._record_retryable_failure(operation_name)
                if attempt >= self.backoff.max_attempts:
                    raise RetryBudgetExceeded(
                        f"{operation_name} exhausted retry budget"
                    ) from exc
                self.sleep(self._full_jitter_delay(retry_index=attempt))
            else:
                self._consecutive_failures = 0
                return result
        raise AssertionError("retry loop exited unexpectedly")

    def wrap(
        self,
        operation_name: str,
        operation: Callable[..., T],
        *,
        request_kind: RequestKind = "read",
    ) -> Callable[..., T]:
        """Return an adapter-method wrapper that applies this policy."""

        def wrapped(*args: object, **kwargs: object) -> T:
            return self.execute(
                operation_name,
                lambda: operation(*args, **kwargs),
                request_kind=request_kind,
            )

        return wrapped

    def _full_jitter_delay(self, *, retry_index: int) -> float:
        cap = _capped_exponential_delay(self.backoff, retry_index)
        return self.random() * cap

    def _record_retryable_failure(self, operation_name: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_threshold:
            self._circuit_opened_at = self.clock()
            raise CircuitBreakerOpen(
                f"{operation_name} paused after repeated QQ Music failures",
                retry_after_seconds=self.circuit_breaker_cooldown_seconds,
            )

    def _raise_if_circuit_open(self, operation_name: str) -> None:
        if self._circuit_opened_at is None:
            return
        elapsed = self.clock() - self._circuit_opened_at
        if elapsed < self.circuit_breaker_cooldown_seconds:
            raise CircuitBreakerOpen(
                f"{operation_name} paused while QQ Music circuit breaker is open",
                retry_after_seconds=self.circuit_breaker_cooldown_seconds - elapsed,
            )
        self._circuit_opened_at = None
        self._consecutive_failures = 0


def _is_retryable(category: RetryCategory) -> bool:
    return category in {
        RetryCategory.TRANSIENT_NETWORK,
        RetryCategory.THROTTLED,
        RetryCategory.TEMPORARY_SERVER,
    }


def _capped_exponential_delay(backoff: BackoffConfig, retry_index: int) -> float:
    uncapped = backoff.initial_seconds * (backoff.multiplier ** (retry_index - 1))
    return min(backoff.max_seconds, uncapped)


__all__ = [
    "AuthenticationFailure",
    "BackoffConfig",
    "CircuitBreakerOpen",
    "QQMusicRateLimitPolicy",
    "RateLimitExceeded",
    "RequestPacer",
    "RetryBudgetExceeded",
    "RetryCategory",
    "RetryPolicyError",
    "RollingWindowLimiter",
    "SpotifyRateLimitPolicy",
    "TemporaryServerError",
    "TransientNetworkError",
    "ValidationFailure",
]
