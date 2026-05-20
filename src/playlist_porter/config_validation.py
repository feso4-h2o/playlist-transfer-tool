"""Static validation helpers for local CLI configuration."""

from __future__ import annotations

from typing import Literal

PlatformName = Literal["mock", "spotify", "qqmusic"]
DestinationTargetType = Literal["playlist", "liked_songs"]


def validate_write_target_config(
    *,
    destination_platform: str,
    destination_target_type: str,
    destination_playlist_id: str | None,
    create_playlist: str | None,
) -> None:
    """Validate write target config without authenticating or calling platform APIs."""

    target_type = _destination_target_type(destination_target_type)
    target_id = _optional_text(destination_playlist_id)
    create_name = _optional_text(create_playlist)

    if target_id is not None and create_name is not None:
        raise ValueError("choose either destination_playlist_id or create_playlist, not both")
    if target_type == "playlist":
        return
    if create_name is not None:
        raise ValueError("create_playlist is only supported for playlist destination targets")
    if destination_platform == "spotify" and target_id is not None:
        raise ValueError("Spotify Liked Songs does not accept destination_playlist_id")
    if destination_platform == "qqmusic" and target_id is None:
        raise ValueError("QQ Music liked_songs writes require destination_playlist_id")


def _destination_target_type(value: str | None) -> DestinationTargetType:
    target_type = _optional_text(value) or "playlist"
    if target_type not in {"playlist", "liked_songs"}:
        raise ValueError("destination_target_type must be one of playlist, liked_songs")
    return target_type


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["validate_write_target_config"]
