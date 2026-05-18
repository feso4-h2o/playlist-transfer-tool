import csv
import json

import pytest

from playlist_porter.cli import build_parser, main
from playlist_porter.config import PorterConfig
from playlist_porter.matching.status import MatchStatus
from playlist_porter.persistence import exports as exports_module
from playlist_porter.persistence.exports import build_unavailable_rows
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.rate_limit import AuthenticationFailure
from playlist_porter.workflow import run_transfer


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _config_file(tmp_path, *, playlist_tracks, catalog_tracks, commands=None) -> tuple:
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
                    "tracks": playlist_tracks,
                }
            ]
        },
    )
    _write_json(catalog_path, {"catalog": catalog_tracks})
    payload = {
        "database_path": str(database_path),
        "destination_platform": "mock",
        "report_output_dir": str(reports_path),
        "report_format": "json",
        "run_id": "",
        "source_platform": "mock",
        "mock": {
            "source_playlists_path": str(playlists_path),
            "destination_catalog_path": str(catalog_path),
            "writes_path": str(writes_path),
        },
        "commands": {
            "match": {
                "source_playlist": "source-playlist",
                "restart": False,
            }
        },
    }
    if commands is not None:
        payload["commands"] = commands
    _write_json(config_path, payload)
    return config_path, database_path, writes_path, reports_path


def _run_mock_match(config: PorterConfig, source_playlist_id: str = "source-playlist"):
    return run_transfer(
        config,
        source_platform="mock",
        destination_platform="mock",
        source_playlist_id=source_playlist_id,
        dry_run=True,
    )


def _set_config_values(config_path, **values) -> dict:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload.update(values)
    _write_json(config_path, payload)
    return payload


def _phase4_fixture(tmp_path):
    return _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-exact",
                "title": "Alpha",
                "artists": ["Exact Artist"],
                "duration_seconds": 180,
                "isrc": "ISRC1",
            },
            {
                "id": "source-review",
                "title": "Beta",
                "artists": ["Review Artist"],
                "duration_seconds": 180,
            },
            {
                "id": "source-region",
                "title": "Territory",
                "artists": ["Region Artist"],
                "duration_seconds": 180,
            },
            {
                "id": "source-missing",
                "title": "Qxzv",
                "artists": ["No Match Artist"],
                "duration_seconds": 180,
            },
        ],
        catalog_tracks=[
            {
                "id": "dest-exact",
                "title": "Alpha",
                "artists": ["Exact Artist"],
                "duration_seconds": 180,
                "isrc": "ISRC1",
            },
            {
                "id": "dest-review",
                "title": "Beta (Remix)",
                "artists": ["Review Artist"],
                "duration_seconds": 180,
            },
            {
                "id": "dest-region",
                "title": "Territory",
                "artists": ["Region Artist"],
                "duration_seconds": 180,
                "unavailable_reason": "region_unavailable",
            },
        ],
    )


def test_cli_match_persists_decisions_without_writes(tmp_path) -> None:
    config_path, database_path, writes_path, _ = _phase4_fixture(tmp_path)

    exit_code = main(["match", "--config", str(config_path)])

    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    decisions = repo.load_match_decisions(run_id)

    assert exit_code == 0
    assert writes_path.exists() is False
    assert json.loads(config_path.read_text(encoding="utf-8"))["run_id"] == run_id
    assert [decision.status for decision in decisions] == [
        MatchStatus.ISRC_EXACT,
        MatchStatus.NEEDS_REVIEW,
        MatchStatus.NOT_FOUND,
        MatchStatus.NOT_FOUND,
    ]


def test_mock_source_track_link_evidence_persists_to_match_decision(tmp_path) -> None:
    config_path, database_path, _, _ = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "200030089:1",
                "platform": "qqmusic",
                "title": "Source",
                "artists": ["Artist"],
                "duration_seconds": 180,
                "qqmusic_songmid": "001abcDEFghi",
            }
        ],
        catalog_tracks=[],
    )

    main(["match", "--config", str(config_path)])

    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    decision = repo.load_match_decisions(run_id)[0]
    assert decision.source_track.platform_track_id == "200030089:1"
    assert decision.evidence["qqmusic_songmid"] == "001abcDEFghi"
    assert (
        decision.evidence["qqmusic_url"]
        == "https://y.qq.com/n/ryqq/songDetail/001abcDEFghi"
    )


def test_repeated_dry_run_refreshes_existing_fixture_tracks(tmp_path) -> None:
    config_path, database_path, _, _ = _phase4_fixture(tmp_path)

    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    first_metrics = repo.load_metrics(run_id)

    main(["match", "--config", str(config_path)])

    second_metrics = repo.load_metrics(run_id)
    assert second_metrics.source_track_count == first_metrics.source_track_count
    assert second_metrics.candidate_count == first_metrics.candidate_count
    assert len(repo.load_source_tracks(run_id)) == 4
    assert len(repo.load_match_decisions(run_id)) == 4


def test_repeated_dry_run_removes_stale_playlist_tracks(tmp_path) -> None:
    config_path, database_path, writes_path, _ = _phase4_fixture(tmp_path)
    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    removed_track = next(
        track
        for track in repo.load_source_tracks(run_id)
        if track.platform_track_id == "source-missing"
    )
    repo.save_user_override(
        run_id,
        removed_track.internal_id,
        status=MatchStatus.USER_REJECTED,
    )
    repo.record_write_success(run_id, removed_track.internal_id, "obsolete-destination")

    playlists_path = tmp_path / "fixtures" / "playlists.json"
    playlist_payload = json.loads(playlists_path.read_text(encoding="utf-8"))
    playlist_payload["playlists"][0]["tracks"] = [
        track
        for track in playlist_payload["playlists"][0]["tracks"]
        if track["id"] != "source-missing"
    ]
    _write_json(playlists_path, playlist_payload)
    main(["match", "--config", str(config_path)])

    metrics = repo.load_metrics(run_id)
    assert metrics.source_track_count == 3
    assert metrics.user_rejected_count == 0
    assert metrics.write_success_count == 0
    assert [track.platform_track_id for track in repo.load_source_tracks(run_id)] == [
        "source-exact",
        "source-review",
        "source-region",
    ]
    assert [
        decision.source_track.platform_track_id
        for decision in repo.load_match_decisions(run_id)
    ] == [
        "source-exact",
        "source-review",
        "source-region",
    ]
    assert writes_path.exists() is False


def test_repeated_dry_run_preserves_current_review_and_write_state(tmp_path) -> None:
    config_path, database_path, _, _ = _phase4_fixture(tmp_path)
    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    decisions = repo.load_match_decisions(run_id)
    review_decision = next(
        decision
        for decision in decisions
        if decision.source_track.platform_track_id == "source-review"
    )
    exact_decision = next(
        decision
        for decision in decisions
        if decision.source_track.platform_track_id == "source-exact"
    )
    assert review_decision.candidates

    repo.save_user_override(
        run_id,
        review_decision.source_track.internal_id,
        status=MatchStatus.USER_APPROVED,
        selected_candidate=review_decision.candidates[0],
    )
    repo.record_write_success(
        run_id,
        exact_decision.source_track.internal_id,
        "dest-exact",
    )
    main(["match", "--config", str(config_path)])

    metrics = repo.load_metrics(run_id)
    assert metrics.user_approved_count == 1
    assert metrics.write_success_count == 1
    assert repo.load_user_override(
        run_id,
        review_decision.source_track.internal_id,
    ) is not None
    assert repo.should_write_track(
        run_id,
        exact_decision.source_track.internal_id,
        "dest-exact",
    ) is False


def test_review_action_updates_sqlite_override(tmp_path) -> None:
    config_path, database_path, _, _ = _phase4_fixture(tmp_path)
    result = _run_mock_match(
        PorterConfig(
            database_path=database_path,
            report_output_dir=tmp_path / "reports",
            mock_source_playlists_path=tmp_path / "fixtures" / "playlists.json",
            mock_destination_catalog_path=tmp_path / "fixtures" / "catalog.json",
            mock_writes_path=tmp_path / "state" / "writes.json",
        )
    )
    _set_config_values(config_path, run_id=result.transfer_run_id)
    repo = TransferRepository(database_path)
    review_decision = next(
        decision
        for decision in repo.load_match_decisions(result.transfer_run_id)
        if decision.status is MatchStatus.NEEDS_REVIEW
    )

    exit_code = main(
        [
            "review",
            "--config",
            str(config_path),
            "--source-track-id",
            str(review_decision.source_track.internal_id),
            "--action",
            "accept",
            "--candidate-rank",
            "1",
        ]
    )

    override = repo.load_user_override(
        result.transfer_run_id,
        review_decision.source_track.internal_id,
    )
    assert exit_code == 0
    assert override is not None
    assert override.status is MatchStatus.USER_APPROVED


def test_cli_prints_operational_errors_without_traceback(tmp_path, monkeypatch, capsys) -> None:
    config_path, _, _, _ = _phase4_fixture(tmp_path)

    def fail_transfer(*args, **kwargs):
        del args, kwargs
        raise AuthenticationFailure("clear spotify auth error")

    monkeypatch.setattr("playlist_porter.cli.run_transfer", fail_transfer)

    exit_code = main(["match", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == "clear spotify auth error\n"
    assert "Traceback" not in captured.err
    assert json.loads(config_path.read_text(encoding="utf-8"))["run_id"] == ""


def test_match_uses_config_defaults_when_cli_args_are_omitted(tmp_path) -> None:
    config_path, database_path, _, reports_path = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        catalog_tracks=[
            {
                "id": "dest-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        commands={
            "match": {
                "source_playlist": "source-playlist",
                "restart": False,
            }
        },
    )

    exit_code = main(["match", "--config", str(config_path)])

    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert exit_code == 0
    assert run_id is not None
    report_dir = reports_path / run_id[:8]
    assert list(report_dir.glob("summary-*-match.json"))
    assert json.loads(config_path.read_text(encoding="utf-8"))["run_id"] == run_id


def test_config_owned_match_cli_args_are_rejected(tmp_path) -> None:
    config_path, _, _, _ = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        catalog_tracks=[
            {
                "id": "dest-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        commands={
            "match": {
                "source_playlist": "source-playlist",
                "restart": False,
            }
        },
    )

    with pytest.raises(SystemExit) as exc_info:
        main(["match", "--config", str(config_path), "--source-platform", "mock"])

    assert exc_info.value.code == 2


def test_removed_write_commands_are_not_parser_choices(capsys) -> None:
    parser = build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")

    assert {"dry-run", "transfer", "execute", "resume"}.isdisjoint(command_action.choices)

    with pytest.raises(SystemExit) as exc_info:
        main(["hello"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "invalid choice: 'hello'" in captured.err
    assert "transfer" not in captured.err
    assert "execute" not in captured.err
    assert "resume" not in captured.err


def test_match_requires_source_playlist_from_config_or_cli(tmp_path) -> None:
    config_path, _, _, _ = _config_file(
        tmp_path,
        playlist_tracks=[],
        catalog_tracks=[],
        commands={
            "match": {
            }
        },
    )

    with pytest.raises(SystemExit, match="match.source_playlist is required"):
        main(["match", "--config", str(config_path)])


def test_write_requires_run_id_from_config_or_cli(tmp_path) -> None:
    config_path, _, _, _ = _config_file(
        tmp_path,
        playlist_tracks=[],
        catalog_tracks=[],
        commands={
            "write": {
                "destination_platform": "mock",
            }
        },
    )

    with pytest.raises(SystemExit, match="run_id is required"):
        main(["write", "--config", str(config_path)])


def test_write_skips_duplicate_destination_writes(tmp_path) -> None:
    config_path, database_path, writes_path, _ = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-1",
                "title": "Shared Song",
                "artists": ["Artist"],
                "duration_seconds": 180,
            },
            {
                "id": "source-2",
                "title": "Shared Song",
                "artists": ["Artist"],
                "duration_seconds": 180,
            },
        ],
        catalog_tracks=[
            {
                "id": "dest-shared",
                "title": "Shared Song",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
    )
    config = PorterConfig(
        database_path=database_path,
        report_output_dir=tmp_path / "reports",
        mock_source_playlists_path=tmp_path / "fixtures" / "playlists.json",
        mock_destination_catalog_path=tmp_path / "fixtures" / "catalog.json",
        mock_writes_path=writes_path,
    )
    dry_run = _run_mock_match(config)
    _set_config_values(
        config_path,
        run_id=dry_run.transfer_run_id,
        commands={"write": {"create_playlist": "Copied"}},
    )

    first_write = main(["write", "--config", str(config_path)])
    second_write = main(["write", "--config", str(config_path)])

    writes = json.loads(writes_path.read_text(encoding="utf-8"))
    assert first_write == 0
    assert second_write == 0
    assert list(writes.values())[0]["track_ids"] == ["dest-shared"]
    assert TransferRepository(database_path).load_metrics(
        dry_run.transfer_run_id
    ).write_success_count == 1


def test_write_uses_configured_run_defaults(tmp_path) -> None:
    config_path, database_path, writes_path, _ = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        catalog_tracks=[
            {
                "id": "dest-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
    )
    config = PorterConfig(
        database_path=database_path,
        report_output_dir=tmp_path / "reports",
        mock_source_playlists_path=tmp_path / "fixtures" / "playlists.json",
        mock_destination_catalog_path=tmp_path / "fixtures" / "catalog.json",
        mock_writes_path=writes_path,
    )
    dry_run = _run_mock_match(config)
    _set_config_values(
        config_path,
        run_id=dry_run.transfer_run_id,
        commands={"write": {"create_playlist": "Copied"}},
    )

    write = main(["write", "--config", str(config_path)])
    rerun = main(["write", "--config", str(config_path)])

    writes = json.loads(writes_path.read_text(encoding="utf-8"))
    assert write == 0
    assert rerun == 0
    assert list(writes.values())[0]["track_ids"] == ["dest-1"]


def test_write_persists_supplied_destination_playlist_for_rerun(tmp_path) -> None:
    config_path, database_path, writes_path, _ = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        catalog_tracks=[
            {
                "id": "dest-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
    )
    config = PorterConfig(
        database_path=database_path,
        report_output_dir=tmp_path / "reports",
        mock_source_playlists_path=tmp_path / "fixtures" / "playlists.json",
        mock_destination_catalog_path=tmp_path / "fixtures" / "catalog.json",
        mock_writes_path=writes_path,
    )
    dry_run = _run_mock_match(config)
    _write_json(
        writes_path,
        {"existing-playlist": {"name": "Existing", "description": None, "track_ids": []}},
    )
    _set_config_values(
        config_path,
        run_id=dry_run.transfer_run_id,
        commands={"write": {"destination_playlist_id": "existing-playlist"}},
    )

    first_write = main(["write", "--config", str(config_path)])
    second_write = main(["write", "--config", str(config_path)])

    writes = json.loads(writes_path.read_text(encoding="utf-8"))
    assert first_write == 0
    assert second_write == 0
    assert TransferRepository(database_path).load_run(
        dry_run.transfer_run_id
    ).destination_playlist_id == "existing-playlist"
    assert set(writes) == {"existing-playlist"}
    assert writes["existing-playlist"]["track_ids"] == ["dest-1"]


def test_config_owned_write_cli_args_are_rejected(tmp_path) -> None:
    config_path, _, _, _ = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        catalog_tracks=[
            {
                "id": "dest-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main(["write", "--config", str(config_path), "--run-id", "run"])

    assert exc_info.value.code == 2


def test_config_owned_review_pending_only_cli_arg_is_rejected(tmp_path) -> None:
    config_path, _, _, _ = _phase4_fixture(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["review", "--config", str(config_path), "--pending-only"])

    assert exc_info.value.code == 2


def test_write_rejects_conflicting_config_target_defaults(tmp_path, capsys) -> None:
    config_path, database_path, writes_path, _ = _config_file(
        tmp_path,
        playlist_tracks=[
            {
                "id": "source-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
        catalog_tracks=[
            {
                "id": "dest-1",
                "title": "Alpha",
                "artists": ["Artist"],
                "duration_seconds": 180,
            }
        ],
    )
    config = PorterConfig(
        database_path=database_path,
        report_output_dir=tmp_path / "reports",
        mock_source_playlists_path=tmp_path / "fixtures" / "playlists.json",
        mock_destination_catalog_path=tmp_path / "fixtures" / "catalog.json",
        mock_writes_path=writes_path,
    )
    dry_run = _run_mock_match(config)
    _set_config_values(
        config_path,
        run_id=dry_run.transfer_run_id,
        commands={
            "write": {
                "destination_playlist_id": "existing-playlist",
                "create_playlist": "Copied",
            },
        },
    )

    exit_code = main(["write", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "choose either destination_playlist_id or create_playlist" in captured.out


def test_export_reports_include_expected_columns_and_region_reason(tmp_path) -> None:
    config_path, database_path, _, reports_path = _phase4_fixture(tmp_path)
    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    _set_config_values(config_path, run_id=run_id, report_format="both")

    exit_code = main(["export-report", "--config", str(config_path)])

    rows = build_unavailable_rows(repo, run_id)
    report_dir = reports_path / run_id[:8]
    csv_path = next(report_dir.glob("unavailable-*-export-report.csv"))
    json_path = next(report_dir.glob("unavailable-*-export-report.json"))
    csv_rows = list(csv.DictReader(csv_path.open()))
    json_rows = json.loads(json_path.read_text())
    assert exit_code == 0
    assert "region_unavailable" in {
        reason for row in rows for reason in row["reason_codes"].split(";")
    }
    assert csv_rows[0].keys() >= {"source_title", "reason_codes", "top_suggested_alternates"}
    assert json_rows == rows


def test_review_and_export_report_use_config_defaults(tmp_path) -> None:
    config_path, database_path, _, reports_path = _phase4_fixture(tmp_path)
    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    review_decision = next(
        decision
        for decision in repo.load_match_decisions(run_id)
        if decision.status is MatchStatus.NEEDS_REVIEW
    )
    _set_config_values(config_path, run_id=run_id, report_format="json")

    review = main(
        [
            "review",
            "--config",
            str(config_path),
            "--source-track-id",
            str(review_decision.source_track.internal_id),
            "--action",
            "accept",
        ]
    )
    export = main(["export-report", "--config", str(config_path)])

    assert review == 0
    assert export == 0
    report_dir = reports_path / run_id[:8]
    assert list(report_dir.glob("unavailable-*-export-report.json"))


def test_review_uses_configured_pending_only_default(tmp_path, monkeypatch) -> None:
    config_path, database_path, _, _ = _phase4_fixture(tmp_path)
    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    _set_config_values(
        config_path,
        run_id=run_id,
        commands={"review": {"pending_only": True}},
    )
    calls = []

    def fake_review(repository, transfer_run_id, *, pending_only=False, console=None):
        del repository, console
        calls.append((transfer_run_id, pending_only))
        return 0

    monkeypatch.setattr("playlist_porter.cli.run_interactive_review", fake_review)

    exit_code = main(["review", "--config", str(config_path)])

    assert exit_code == 0
    assert calls == [(run_id, True)]


def test_export_reports_do_not_overwrite_same_second_snapshot(tmp_path, monkeypatch) -> None:
    config_path, database_path, _, reports_path = _phase4_fixture(tmp_path)
    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    _set_config_values(config_path, run_id=run_id, report_format="json")
    monkeypatch.setattr(exports_module, "_short_timestamp", lambda: "143022")

    first = main(["export-report", "--config", str(config_path)])
    second = main(["export-report", "--config", str(config_path)])

    report_dir = reports_path / run_id[:8]
    assert first == 0
    assert second == 0
    assert (report_dir / "summary-143022-export-report.json").exists()
    assert (report_dir / "summary-143022-export-report-2.json").exists()


def test_existing_run_direction_must_match_top_level_config(tmp_path, capsys) -> None:
    config_path, database_path, _, _ = _phase4_fixture(tmp_path)
    main(["match", "--config", str(config_path)])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    _set_config_values(config_path, run_id=run_id, destination_platform="spotify")

    exit_code = main(["export-report", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "configured destination_platform spotify does not match" in captured.out
