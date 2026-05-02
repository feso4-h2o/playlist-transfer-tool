import pytest

from playlist_porter.platforms.base import PlatformCapabilities
from playlist_porter.platforms.qqmusic import (
    QQMusicAdapter,
    QQMusicConfig,
    QQMusicWriteUnsupported,
    playlist_from_qqmusic_payload,
    search_tracks_from_qqmusic_payload,
    track_from_qqmusic_payload,
)
from playlist_porter.rate_limit import (
    AuthenticationFailure,
    BackoffConfig,
    CircuitBreakerOpen,
    QQMusicRateLimitPolicy,
    RequestPacer,
    TransientNetworkError,
    ValidationFailure,
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


class LoginExpiredError(Exception):
    pass


class NetworkError(Exception):
    pass


class FakeQQMusicClient:
    def __init__(
        self,
        *,
        playlist_payload: dict | None = None,
        search_payload: dict | None = None,
        validate_error: Exception | None = None,
        search_errors_before_success: int = 0,
        search_error: Exception | None = None,
    ) -> None:
        self.playlist_payload = playlist_payload or {}
        self.search_payload = search_payload or {"song": []}
        self.validate_error = validate_error
        self.search_errors_before_success = search_errors_before_success
        self.search_error = search_error or TransientNetworkError("temporary QQ search failure")
        self.created_names: list[str] = []
        self.added_songs: list[tuple[int, list[tuple[int, int]]]] = []
        self.search_calls = 0

    def validate_session(self) -> None:
        if self.validate_error is not None:
            raise self.validate_error

    def get_playlist(self, playlist_id: int, *, page_size: int) -> dict:
        assert playlist_id == 12345
        assert page_size == 50
        return self.playlist_payload

    def search_tracks(self, query: str, *, limit: int) -> dict:
        assert query == "七里香 周杰伦"
        assert limit == 2
        self.search_calls += 1
        if self.search_calls <= self.search_errors_before_success:
            raise self.search_error
        return self.search_payload

    def create_playlist(self, name: str) -> dict:
        self.created_names.append(name)
        return {"dirid": 777, "name": name}

    def add_songs(self, playlist_id: int, song_info: list[tuple[int, int]]) -> bool:
        self.added_songs.append((playlist_id, song_info))
        return True


def qq_policy(clock: FakeClock | None = None) -> QQMusicRateLimitPolicy:
    clock = clock or FakeClock()
    return QQMusicRateLimitPolicy(
        pacer=RequestPacer(read_interval_seconds=0, write_interval_seconds=0),
        backoff=BackoffConfig(max_attempts=3, initial_seconds=1, max_seconds=10),
        circuit_breaker_threshold=5,
        clock=clock.monotonic,
        sleep=clock.sleep,
        random=lambda: 0.0,
    )


def song_payload(**overrides: object) -> dict:
    payload = {
        "id": 1048576,
        "mid": "003OUlho2HcRHC",
        "type": 0,
        "title": "七里香",
        "singer": [{"name": "周杰伦"}],
        "album": {"name": "七里香"},
        "interval": 298,
        "time_public": "2004-08-03",
    }
    payload.update(overrides)
    return payload


def test_qqmusic_track_mapping_preserves_core_metadata() -> None:
    track = track_from_qqmusic_payload(song_payload(), source_playlist_position=3)

    assert track.platform == "qqmusic"
    assert track.platform_track_id == "1048576:0"
    assert track.title == "七里香"
    assert track.artists == ["周杰伦"]
    assert track.album == "七里香"
    assert track.duration_seconds == 298
    assert track.release_year == 2004
    assert track.isrc is None
    assert track.source_playlist_position == 3


def test_qqmusic_playlist_mapping_uses_detail_response_shape() -> None:
    playlist = playlist_from_qqmusic_payload(
        {
            "info": {"id": 12345, "title": "华语收藏", "desc": "local test"},
            "songs": [
                song_payload(title="轨道一", id=1, singer=[{"name": "歌手一"}]),
                song_payload(title="轨道二", id=2, singer=[{"name": "歌手二"}]),
            ],
        }
    )

    assert playlist.platform == "qqmusic"
    assert playlist.platform_playlist_id == "12345"
    assert playlist.name == "华语收藏"
    assert [track.source_playlist_position for track in playlist.tracks] == [1, 2]


def test_qqmusic_search_mapping_returns_tracks_from_song_field() -> None:
    tracks = search_tracks_from_qqmusic_payload(
        {"song": [song_payload(id=1), song_payload(id=2, title="晴天")]}
    )

    assert [track.title for track in tracks] == ["七里香", "晴天"]


def test_qqmusic_adapter_exposes_explicit_capability_flags() -> None:
    adapter = QQMusicAdapter(
        config=QQMusicConfig(supports_create_playlist=False, supports_add_tracks=False),
        client=FakeQQMusicClient(),
        rate_limit_policy=qq_policy(),
    )

    assert adapter.capabilities == PlatformCapabilities(
        supports_read=True,
        supports_search=True,
        supports_write=False,
        supports_isrc=False,
        is_official=False,
    )
    with pytest.raises(QQMusicWriteUnsupported):
        adapter.create_playlist("copy")


def test_qqmusic_adapter_fetches_playlist_through_rate_policy() -> None:
    client = FakeQQMusicClient(
        playlist_payload={
            "info": {"id": 12345, "title": "华语收藏"},
            "songs": [song_payload(id=1)],
        }
    )
    adapter = QQMusicAdapter(
        config=QQMusicConfig(page_size=50),
        client=client,
        rate_limit_policy=qq_policy(),
    )

    playlist = adapter.get_playlist("https://y.qq.com/n/ryqq/playlist/12345")

    assert playlist.name == "华语收藏"
    assert playlist.tracks[0].platform_track_id == "1:0"


def test_qqmusic_adapter_search_retries_transient_failures() -> None:
    clock = FakeClock()
    client = FakeQQMusicClient(
        search_payload={"song": [song_payload(id=1), song_payload(id=2, title="晴天")]},
        search_errors_before_success=1,
    )
    adapter = QQMusicAdapter(client=client, rate_limit_policy=qq_policy(clock))

    candidates = adapter.search_tracks("七里香 周杰伦", limit=2)

    assert client.search_calls == 2
    assert clock.sleeps == [0.0]
    assert [candidate.rank for candidate in candidates] == [1, 2]
    assert candidates[0].evidence["qqmusic_capability"] == "search_by_type"


def test_qqmusic_adapter_classifies_raw_network_errors_before_retry() -> None:
    client = FakeQQMusicClient(
        search_payload={"song": [song_payload(id=1)]},
        search_errors_before_success=1,
        search_error=NetworkError("socket reset"),
    )
    adapter = QQMusicAdapter(client=client, rate_limit_policy=qq_policy())

    candidates = adapter.search_tracks("七里香 周杰伦", limit=2)

    assert client.search_calls == 2
    assert candidates[0].track.title == "七里香"


def test_qqmusic_auth_failure_is_non_retryable() -> None:
    adapter = QQMusicAdapter(
        client=FakeQQMusicClient(validate_error=LoginExpiredError("cookie expired")),
        rate_limit_policy=qq_policy(),
    )

    with pytest.raises(AuthenticationFailure, match="cookie expired"):
        adapter.authenticate()


def test_qqmusic_search_circuit_breaker_is_preserved() -> None:
    clock = FakeClock()
    policy = QQMusicRateLimitPolicy(
        pacer=RequestPacer(read_interval_seconds=0, write_interval_seconds=0),
        backoff=BackoffConfig(max_attempts=4, initial_seconds=1, max_seconds=10),
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown_seconds=30,
        clock=clock.monotonic,
        sleep=clock.sleep,
        random=lambda: 1.0,
    )
    adapter = QQMusicAdapter(
        client=FakeQQMusicClient(search_errors_before_success=10),
        rate_limit_policy=policy,
    )

    with pytest.raises(CircuitBreakerOpen) as exc_info:
        adapter.search_tracks("七里香 周杰伦", limit=2)

    assert exc_info.value.retry_after_seconds == 30
    assert clock.sleeps == [1]


def test_qqmusic_add_tracks_sends_numeric_song_info_pairs() -> None:
    client = FakeQQMusicClient()
    adapter = QQMusicAdapter(client=client, rate_limit_policy=qq_policy())

    adapter.add_tracks("777", ["1048576:0", "2048:13"])

    assert client.added_songs == [(777, [(1048576, 0), (2048, 13)])]


def test_qqmusic_add_tracks_rejects_non_numeric_track_ids() -> None:
    adapter = QQMusicAdapter(client=FakeQQMusicClient(), rate_limit_policy=qq_policy())

    with pytest.raises(ValidationFailure, match="numeric song ids"):
        adapter.add_tracks("777", ["mid-only"])
