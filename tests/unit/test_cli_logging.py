import json

from loguru import logger

from playlist_porter.cli import build_parser, main
from playlist_porter.diagnostics import diagnostic_logger
from playlist_porter.logging_config import REDACTED, configure_logging, redact
from playlist_porter.persistence.repositories import TransferRepository


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _latest_log_file(tmp_path):
    return max((tmp_path / "logs").glob("playlist-porter-debug-*.log"))


def _log_records(path):
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(" | ")
        data = json.loads(parts[-1]) if parts[-1].startswith("{") else {}
        head = parts[:-1] if data else parts
        if len(head) == 3:
            timestamp, level, message = head
            scope = None
        elif len(head) == 4:
            timestamp, level, scope, message = head
        else:
            raise AssertionError(f"malformed log line: {line}")
        record = {
            "time": timestamp,
            "level": level,
            "message": message,
            "scope": scope,
            "data": data,
            "line": line,
        }
        records.append(record)
    return records


def _messages(records):
    return [record["message"] for record in records]


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
                            "album": "Source Album",
                            "isrc": "ISRC1",
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
                    "album": "Source Album",
                    "isrc": "ISRC1",
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
    for line in log_text.splitlines():
        assert " | " in line
        assert not line.startswith("{")


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


def test_diagnostic_records_are_file_only(tmp_path, capsys) -> None:
    setup = configure_logging(verbosity=2, debug_log=True, log_dir=tmp_path / "logs")
    assert setup.log_path is not None

    diagnostic_logger("match").debug("diagnostic detail", song="Alpha")
    logger.debug("console debug")

    captured = capsys.readouterr()
    records = _log_records(setup.log_path)
    assert "diagnostic detail" not in captured.err
    assert "console debug" in captured.err
    assert "diagnostic detail" in _messages(records)
    diagnostic_record = next(
        record for record in records if record["message"] == "diagnostic detail"
    )
    assert diagnostic_record["scope"] == "match"


def test_debug_log_includes_match_diagnostics(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _config_file(tmp_path)

    exit_code = main(
        [
            "dry-run",
            "--config",
            str(config_path),
            "--source-playlist",
            "source-playlist",
            "-vv",
            "-l",
        ]
    )

    captured = capsys.readouterr()
    records = _log_records(_latest_log_file(tmp_path))
    messages = _messages(records)
    assert exit_code == 0
    assert "track matching finished" not in captured.err
    assert "track matching finished" in messages
    match_record = next(
        record for record in records if record["message"] == "track matching finished"
    )
    decision = match_record["data"]["decision"]
    assert match_record["scope"] == "match"
    assert decision["source_track"]["title"] == "Alpha"
    assert decision["source_track"]["artists"] == ["Artist"]
    assert decision["source_track"]["album"] == "Source Album"
    assert decision["source_track"]["isrc"] == "ISRC1"
    assert decision["status"] == "isrc_exact"
    assert decision["selected_candidate"]["platform_track_id"] == "dest-1"
    assert decision["selected_candidate"]["evidence"]["title_score"] == 1.0


def test_debug_log_includes_review_diagnostics(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _config_file(tmp_path)
    assert main(
        ["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"]
    ) == 0
    repo = TransferRepository(tmp_path / "state" / "transfer.sqlite")
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    decision = repo.load_match_decisions(run_id)[0]

    exit_code = main(
        [
            "review",
            "--db",
            str(tmp_path / "state" / "transfer.sqlite"),
            "--run-id",
            run_id,
            "--source-track-id",
            str(decision.source_track.internal_id),
            "--action",
            "accept",
            "--candidate-rank",
            "99",
            "-l",
        ]
    )

    records = _log_records(_latest_log_file(tmp_path))
    rank_record = next(
        record for record in records if record["message"] == "review candidate rank not found"
    )
    assert exit_code == 1
    assert rank_record["scope"] == "review"
    assert rank_record["data"]["requested_rank"] == 99
    assert rank_record["data"]["decision"]["source_track"]["title"] == "Alpha"


def test_debug_log_includes_write_diagnostics(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _config_file(tmp_path)
    assert main(
        ["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"]
    ) == 0
    repo = TransferRepository(tmp_path / "state" / "transfer.sqlite")
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None

    exit_code = main(
        [
            "write",
            "--config",
            str(config_path),
            "--destination-platform",
            "mock",
            "--run-id",
            run_id,
            "--create-playlist",
            "Copied",
            "-l",
        ]
    )

    records = _log_records(_latest_log_file(tmp_path))
    messages = _messages(records)
    assert exit_code == 0
    assert "write eligibility evaluated" in messages
    assert "write resume eligibility checked" in messages
    assert "track write started" in messages
    assert "track write recorded" in messages
    write_record = next(
        record
        for record in records
        if record["message"] == "track write recorded"
        and record.get("scope") == "write"
    )
    assert write_record["scope"] == "write"
    assert write_record["data"]["pair"]["destination_track_id"] == "dest-1"


def test_debug_log_includes_export_diagnostics(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _config_file(tmp_path)
    assert main(
        ["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"]
    ) == 0
    repo = TransferRepository(tmp_path / "state" / "transfer.sqlite")
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None

    exit_code = main(
        [
            "export-report",
            "--db",
            str(tmp_path / "state" / "transfer.sqlite"),
            "--run-id",
            run_id,
            "--output-dir",
            str(tmp_path / "reports"),
            "--format",
            "json",
            "-l",
        ]
    )

    records = _log_records(_latest_log_file(tmp_path))
    export_record = next(
        record for record in records if record["message"] == "report export completed"
    )
    assert exit_code == 0
    assert export_record["scope"] == "export"
    assert len(export_record["data"]["written_paths"]) == 2


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
