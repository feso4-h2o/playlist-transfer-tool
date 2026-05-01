"""Local JSON configuration for CLI workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PorterConfig:
    """Resolved configuration for the mock CLI workflow."""

    database_path: Path
    report_output_dir: Path
    mock_source_playlists_path: Path
    mock_destination_catalog_path: Path
    mock_writes_path: Path | None = None


def default_config_payload() -> dict[str, Any]:
    """Return a credential-free starter configuration."""

    return {
        "database_path": "state/playlist-porter.sqlite",
        "report_output_dir": "reports",
        "mock": {
            "source_playlists_path": "fixtures/mock-playlists.json",
            "destination_catalog_path": "fixtures/mock-catalog.json",
            "writes_path": "state/mock-writes.json",
        },
    }


def write_default_config(path: str | Path, *, force: bool = False) -> Path:
    """Write a starter JSON config and return its path."""

    target = Path(path)
    if target.exists() and not force:
        raise FileExistsError(f"config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(default_config_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def load_config(path: str | Path) -> PorterConfig:
    """Load and resolve a JSON config relative to its own directory."""

    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    base_dir = config_path.parent
    mock_payload = payload.get("mock", {})

    return PorterConfig(
        database_path=_resolve_path(base_dir, payload["database_path"]),
        report_output_dir=_resolve_path(base_dir, payload.get("report_output_dir", "reports")),
        mock_source_playlists_path=_resolve_path(
            base_dir,
            mock_payload["source_playlists_path"],
        ),
        mock_destination_catalog_path=_resolve_path(
            base_dir,
            mock_payload["destination_catalog_path"],
        ),
        mock_writes_path=(
            _resolve_path(base_dir, mock_payload["writes_path"])
            if mock_payload.get("writes_path")
            else None
        ),
    )


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


__all__ = ["PorterConfig", "default_config_payload", "load_config", "write_default_config"]
