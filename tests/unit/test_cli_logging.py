import json

from loguru import logger

from playlist_porter.cli import build_parser, main
from playlist_porter.logging_config import REDACTED, configure_logging, redact


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _config_file(tmp_path, *, extra_payload=None):
    playlists_path = tmp_path / "fixtures" / "playlists.json"
    catalog_path = tmp_path / "fixtures" / "catalog.json"
    database_path = tmp_path / "state" / "transfer.sqlite"
    writes_path = tmp_path / "state" / "writes.json"
    reports_path = tmp_path / "reports"
    config_path = tmp_path / "porter.json"
    _write_json(
        playlists_path,
        {
            "playlists": [
                {
                    "id": "source-playlist",
                    "name": "Source",
                    "tracks": [
                        {
                            "id": "source-1",
                            "title": "Alpha",
                            "artists": ["Artist"],
                            "duration_seconds": 180,
                        }
                    ],
                }
            ]
        },
    )
    _write_json(
        catalog_path,
        {
            "catalog": [
                {
                    "id": "dest-1",
                    "title": "Alpha",
                    "artists": ["Artist"],
                    "duration_seconds": 180,
                }
            ]
        },
    )
    payload = {
        "database_path": str(database_path),
        "report_output_dir": str(reports_path),
        "mock": {
            "source_playlists_path": str(playlists_path),
            "destination_catalog_path": str(catalog_path),
            "writes_path": str(writes_path),
        },
    }
    if extra_payload:
        payload.update(extra_payload)
    _write_json(config_path, payload)
    return config_path


def test_logging_flags_parse_before_or_after_subcommand() -> None:
    parser = build_parser()

    before = parser.parse_args(
        [
            "-vv",
            "--log",
            "match",
            "--config",
            "porter.json",
            "--source-platform",
            "mock",
            "--destination-platform",
            "mock",
            "--source-playlist",
            "source",
        ]
    )
    after = parser.parse_args(
        [
            "match",
            "--config",
            "porter.json",
            "--source-platform",
            "mock",
            "--destination-platform",
            "mock",
            "--source-playlist",
            "source",
            "-vv",
            "-l",
        ]
    )

    assert before.verbosity == 2
    assert before.debug_log is True
    assert after.verbosity == 2
    assert after.debug_log is True


def test_default_logging_does_not_create_log_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _config_file(tmp_path)

    exit_code = main(
        ["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"]
    )

    assert exit_code == 0
    assert not (tmp_path / "logs").exists()


def test_config_logging_payload_does_not_enable_log_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _config_file(
        tmp_path,
        extra_payload={"logging": {"verbosity": 2, "debug_log": True, "log_dir": "logs"}},
    )

    exit_code = main(
        ["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"]
    )

    assert exit_code == 0
    assert not (tmp_path / "logs").exists()


def test_debug_log_flag_creates_debug_log_without_debug_console(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _config_file(tmp_path)

    exit_code = main(
        ["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist", "-l"]
    )

    captured = capsys.readouterr()
    log_files = list((tmp_path / "logs").glob("playlist-porter-debug-*.log"))
    assert exit_code == 0
    assert captured.err == ""
    assert len(log_files) == 1
    log_text = log_files[0].read_text(encoding="utf-8")
    assert "DEBUG" in log_text
    assert "INFO" in log_text


def test_console_logging_includes_categorized_level_names(capsys) -> None:
    configure_logging(verbosity=2)

    logger.info("info example")
    logger.warning("warning example")
    logger.error("error example")
    logger.debug("debug example")

    captured = capsys.readouterr()
    assert "INFO" in captured.err
    assert "WARNING" in captured.err
    assert "ERROR" in captured.err
    assert "DEBUG" in captured.err


def test_redact_replaces_nested_secret_values() -> None:
    payload = {
        "access_token": "token-value",
        "headers": {
            "Authorization": "Bearer abc123",
            "Cookie": "sessionid=abc123",
        },
        "client_secret": "secret-value",
        "nested": [
            {"refresh_token": "refresh-value"},
            "client_secret=visible-in-source",
        ],
        "platform": "spotify",
        "count": 3,
    }

    redacted = redact(payload)

    assert redacted["access_token"] == REDACTED
    assert redacted["headers"] == REDACTED
    assert redacted["client_secret"] == REDACTED
    assert redacted["nested"][0]["refresh_token"] == REDACTED
    assert redacted["nested"][1] == f"client_secret={REDACTED}"
    assert redacted["platform"] == "spotify"
    assert redacted["count"] == 3
