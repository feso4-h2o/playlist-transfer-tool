import pytest

from playlist_porter.rate_limit import (
    AuthenticationFailure,
    BackoffConfig,
    CircuitBreakerOpen,
    QQMusicRateLimitPolicy,
    RateLimitExceeded,
    RequestPacer,
    RetryBudgetExceeded,
    RollingWindowLimiter,
    SpotifyRateLimitPolicy,
    TemporaryServerError,
    TransientNetworkError,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_spotify_retry_after_is_honored_before_exponential_fallback() -> None:
    clock = FakeClock()
    calls = 0
    policy = SpotifyRateLimitPolicy(
        backoff=BackoffConfig(max_attempts=2, initial_seconds=1, max_seconds=10),
        sleep=clock.sleep,
        random=lambda: 0.0,
    )

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimitExceeded(retry_after_seconds=7)
        return "ok"

    result = policy.execute("spotify search", operation)

    assert result == "ok"
    assert calls == 2
    assert clock.sleeps == [7]


def test_spotify_exponential_fallback_is_capped() -> None:
    clock = FakeClock()
    calls = 0
    policy = SpotifyRateLimitPolicy(
        backoff=BackoffConfig(
            max_attempts=4,
            initial_seconds=2,
            multiplier=3,
            max_seconds=5,
        ),
        sleep=clock.sleep,
        random=lambda: 0.0,
    )

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 4:
            raise TemporaryServerError()
        return "ok"

    assert policy.execute("spotify playlist fetch", operation) == "ok"
    assert clock.sleeps == [2, 5, 5]


def test_spotify_rolling_window_limiter_waits_for_capacity() -> None:
    clock = FakeClock()
    limiter = RollingWindowLimiter(
        max_requests=2,
        window_seconds=30,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )

    limiter.wait_for_slot()
    limiter.wait_for_slot()
    limiter.wait_for_slot()

    assert clock.sleeps == [30]


def test_qq_circuit_breaker_pauses_after_repeated_failures() -> None:
    clock = FakeClock()
    calls = 0
    policy = QQMusicRateLimitPolicy(
        pacer=RequestPacer(read_interval_seconds=0, write_interval_seconds=0),
        backoff=BackoffConfig(max_attempts=4, initial_seconds=1, max_seconds=10),
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown_seconds=45,
        clock=clock.monotonic,
        sleep=clock.sleep,
        random=lambda: 1.0,
    )

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise TransientNetworkError()

    with pytest.raises(CircuitBreakerOpen) as exc_info:
        policy.execute("qqmusic search", operation)

    assert calls == 2
    assert clock.sleeps == [1]
    assert exc_info.value.retry_after_seconds == 45
    with pytest.raises(CircuitBreakerOpen):
        policy.execute("qqmusic search", lambda: "not called")


def test_qq_auth_failures_are_not_retried_or_counted_for_circuit_breaker() -> None:
    clock = FakeClock()
    calls = 0
    policy = QQMusicRateLimitPolicy(
        pacer=RequestPacer(read_interval_seconds=0, write_interval_seconds=0),
        circuit_breaker_threshold=2,
        clock=clock.monotonic,
        sleep=clock.sleep,
        random=lambda: 1.0,
    )

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise AuthenticationFailure("cookie expired")

    with pytest.raises(AuthenticationFailure, match="cookie expired"):
        policy.execute("qqmusic playlist fetch", operation)

    assert calls == 1
    assert clock.sleeps == []
    assert policy.consecutive_failures == 0


def test_qq_retry_budget_exhaustion_preserves_capped_full_jitter() -> None:
    clock = FakeClock()
    policy = QQMusicRateLimitPolicy(
        pacer=RequestPacer(read_interval_seconds=0, write_interval_seconds=0),
        backoff=BackoffConfig(
            max_attempts=3,
            initial_seconds=2,
            multiplier=4,
            max_seconds=5,
        ),
        circuit_breaker_threshold=10,
        clock=clock.monotonic,
        sleep=clock.sleep,
        random=lambda: 0.5,
    )

    with pytest.raises(RetryBudgetExceeded):
        policy.execute("qqmusic write", lambda: (_ for _ in ()).throw(RateLimitExceeded()))

    assert clock.sleeps == [1, 2.5]
