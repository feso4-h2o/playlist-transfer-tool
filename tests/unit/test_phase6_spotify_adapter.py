from __future__ import annotations

import pytest

from playlist_porter.config import SpotifyConfig
from playlist_porter.matching.candidates import generate_candidates
from playlist_porter.matching.status import UnavailableReason
from playlist_porter.models import Playlist, TransferRun, UniversalTrack
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.platforms.spotify import SPOTIFY_BATCH_LIMIT, SpotifyAdapter
from playlist_porter.rate_limit import AuthenticationFailure


class FakeSpotifyClient:
    def __init__(self) -> None:
        self.added_batches: list[tuple[str, list[str]]] = []
        self.playlist_item_offsets: list[int] = []

    def playlist(self, playlist_id, fields=None):
        return {
            "id": playlist_id,
            "name": "Source Playlist",
            "description": "fixture playlist",
            "owner": {"id": "owner-1"},
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{playlist_id}"},
        }

    def playlist_items(self, playlist_id, limit=100, offset=0, additional_types=None):
        del playlist_id, additional_types
        self.playlist_item_offsets.append(offset)
        items = [
            {"track": _spotify_track("track-1", "Alpha", ["Artist"], isrc="USRC17607839")},
            {"track": _spotify_track("track-2", "Beta", ["Artist"], release_date="2020")},
        ]
        page_items = items[offset : offset + limit]
        next_url = "next" if offset + limit < len(items) else None
        return {"items": page_items, "next": next_url}

    def search(self, q, type, limit):
        del type
        if q == "restricted":
            return {
                "tracks": {
                    "items": [
                        _spotify_track(
                            "restricted-track",
                            "Restricted",
                            ["Artist"],
                            is_playable=False,
                            restriction_reason="market",
                        )
                    ]
                }
            }
        return {
            "tracks": {
                "items": [
                    _spotify_track(
                        f"result-{index}",
                        ("Alpha" if index == 0 else "Unrelated"),
                        (["Artist"] if index == 0 else ["Someone Else"]),
                        popularity=80 - index,
                    )
                    for index in range(limit)
                ]
            }
        }

    def current_user(self):
        return {"id": "user-1"}

    def user_playlist_create(self, user, name, public, description):
        assert user == "user-1"
        assert public is False
        return {"id": f"created-{name}-{description}"}

    def playlist_add_items(self, playlist_id, uris):
        self.added_batches.append((playlist_id, list(uris)))
        return {"snapshot_id": f"snapshot-{len(self.added_batches)}"}


def test_spotify_authentication_fails_clearly_without_credentials() -> None:
    adapter = SpotifyAdapter(SpotifyConfig())

    with pytest.raises(AuthenticationFailure, match="client_id, client_secret, redirect_uri"):
        adapter.authenticate()


def test_spotify_playlist_read_maps_paginated_tracks_to_universal_models() -> None:
    client = FakeSpotifyClient()
    adapter = SpotifyAdapter(client=client)

    playlist = adapter.get_playlist("https://open.spotify.com/playlist/playlist-1?si=abc")

    assert playlist.platform == "spotify"
    assert playlist.platform_playlist_id == "playlist-1"
    assert playlist.owner_id == "owner-1"
    assert [track.platform_track_id for track in playlist.tracks] == ["track-1", "track-2"]
    assert playlist.tracks[0].duration_seconds == 181
    assert playlist.tracks[0].isrc == "USRC17607839"
    assert playlist.tracks[0].release_year == 1976
    assert playlist.tracks[0].release_date.isoformat() == "1976-12-08"
    assert playlist.tracks[0].source_playlist_position == 0
    assert playlist.tracks[1].release_year == 2020
    assert playlist.tracks[1].release_date is None


def test_spotify_search_returns_ranked_candidates() -> None:
    adapter = SpotifyAdapter(client=FakeSpotifyClient())

    candidates = adapter.search_tracks("alpha artist", limit=3)

    assert [candidate.rank for candidate in candidates] == [1, 2, 3]
    assert [candidate.track.platform for candidate in candidates] == ["spotify"] * 3
    assert candidates[0].query == "alpha artist"
    assert candidates[0].score > candidates[1].score
    assert candidates[0].evidence["spotify_popularity"] == 80


def test_spotify_search_marks_restricted_tracks_unavailable() -> None:
    adapter = SpotifyAdapter(client=FakeSpotifyClient())

    candidates = adapter.search_tracks("restricted")

    assert candidates[0].unavailable_reason is UnavailableReason.REGION_UNAVAILABLE
    assert candidates[0].evidence["spotify_is_playable"] is False
    assert candidates[0].evidence["spotify_restriction_reason"] == "market"


def test_spotify_search_scores_protect_candidate_truncation() -> None:
    adapter = SpotifyAdapter(client=OutOfOrderSearchClient())
    source_track = UniversalTrack(
        title="Alpha",
        artists=["Artist"],
        platform="mock",
    )

    candidates = generate_candidates(source_track, adapter, limit=1, per_query_limit=2)

    assert candidates[0].track.platform_track_id == "exact"


def test_spotify_add_tracks_batches_by_api_limit() -> None:
    client = FakeSpotifyClient()
    adapter = SpotifyAdapter(client=client)
    track_ids = [f"track-{index}" for index in range(SPOTIFY_BATCH_LIMIT + 1)]

    adapter.add_tracks("playlist-1", track_ids)

    assert [len(batch) for _, batch in client.added_batches] == [
        SPOTIFY_BATCH_LIMIT,
        1,
    ]
    assert client.added_batches[0][1][0] == "spotify:track:track-0"


def test_spotify_write_progress_records_successful_batches_and_resumes(tmp_path) -> None:
    client = FakeSpotifyClient()
    adapter = SpotifyAdapter(client=client)
    repository = TransferRepository(tmp_path / "transfer.sqlite")
    source_tracks = [
        UniversalTrack(title=f"Source {index}", artists=["Artist"], platform="spotify")
        for index in range(3)
    ]
    run = TransferRun(
        source_platform="mock",
        destination_platform="spotify",
        source_playlist=Playlist(name="Source", tracks=source_tracks),
        dry_run=False,
    )
    run_id = repository.create_run(run)
    repository.save_source_playlist(run_id, run.source_playlist)
    source_track_ids = [str(track.internal_id) for track in source_tracks]
    track_ids = ["dest-1", "dest-2", "dest-3"]

    written_count = adapter.add_tracks_with_progress(
        "playlist-1",
        source_track_ids,
        track_ids,
        repository=repository,
        transfer_run_id=run_id,
    )
    resumed_count = adapter.add_tracks_with_progress(
        "playlist-1",
        source_track_ids,
        track_ids,
        repository=repository,
        transfer_run_id=run_id,
    )

    assert written_count == 3
    assert resumed_count == 0
    assert len(client.added_batches) == 1
    assert repository.load_metrics(run_id).write_success_count == 3


def _spotify_track(
    track_id: str,
    name: str,
    artists: list[str],
    *,
    isrc: str | None = None,
    release_date: str = "1976-12-08",
    popularity: int | None = None,
    is_playable: bool | None = None,
    restriction_reason: str | None = None,
) -> dict:
    payload = {
        "id": track_id,
        "uri": f"spotify:track:{track_id}",
        "type": "track",
        "name": name,
        "duration_ms": 180501,
        "explicit": False,
        "popularity": popularity,
        "artists": [{"name": artist} for artist in artists],
        "album": {"name": "Album", "release_date": release_date},
        "external_ids": {"isrc": isrc} if isrc else {},
    }
    if is_playable is not None:
        payload["is_playable"] = is_playable
    if restriction_reason is not None:
        payload["restrictions"] = {"reason": restriction_reason}
    return payload


class OutOfOrderSearchClient(FakeSpotifyClient):
    def search(self, q, type, limit):
        del q, type, limit
        return {
            "tracks": {
                "items": [
                    _spotify_track("wrong", "Gamma", ["Someone Else"], popularity=99),
                    _spotify_track("exact", "Alpha", ["Artist"], popularity=1),
                ]
            }
        }
