import csv
import json

from playlist_porter.cli import main
from playlist_porter.config import PorterConfig
from playlist_porter.matching.status import MatchStatus
from playlist_porter.persistence import exports as exports_module
from playlist_porter.persistence.exports import build_unavailable_rows
from playlist_porter.persistence.repositories import TransferRepository
from playlist_porter.rate_limit import AuthenticationFailure
from playlist_porter.workflow import dry_run_mock_transfer, execute_mock_transfer


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
        "report_output_dir": str(reports_path),
        "mock": {
            "source_playlists_path": str(playlists_path),
            "destination_catalog_path": str(catalog_path),
            "writes_path": str(writes_path),
        },
    }
    if commands is not None:
        payload["commands"] = commands
    _write_json(config_path, payload)
    return config_path, database_path, writes_path, reports_path


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


def test_cli_dry_run_persists_decisions_without_writes(tmp_path) -> None:
    config_path, database_path, writes_path, _ = _phase4_fixture(tmp_path)

    exit_code = main(
        [
            "dry-run",
            "--config",
            str(config_path),
            "--source-playlist",
            "source-playlist",
        ]
    )

    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    decisions = repo.load_match_decisions(run_id)

    assert exit_code == 0
    assert writes_path.exists() is False
    assert [decision.status for decision in decisions] == [
        MatchStatus.ISRC_EXACT,
        MatchStatus.NEEDS_REVIEW,
        MatchStatus.NOT_FOUND,
        MatchStatus.NOT_FOUND,
    ]


def test_repeated_dry_run_refreshes_existing_fixture_tracks(tmp_path) -> None:
    config_path, database_path, _, _ = _phase4_fixture(tmp_path)

    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    first_metrics = repo.load_metrics(run_id)

    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])

    second_metrics = repo.load_metrics(run_id)
    assert second_metrics.source_track_count == first_metrics.source_track_count
    assert second_metrics.candidate_count == first_metrics.candidate_count
    assert len(repo.load_source_tracks(run_id)) == 4
    assert len(repo.load_match_decisions(run_id)) == 4


def test_repeated_dry_run_removes_stale_playlist_tracks(tmp_path) -> None:
    config_path, database_path, writes_path, _ = _phase4_fixture(tmp_path)
    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])
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
    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])

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
    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])
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
    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])

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
    result = dry_run_mock_transfer(
        PorterConfig(
            database_path=database_path,
            report_output_dir=tmp_path / "reports",
            mock_source_playlists_path=tmp_path / "fixtures" / "playlists.json",
            mock_destination_catalog_path=tmp_path / "fixtures" / "catalog.json",
            mock_writes_path=tmp_path / "state" / "writes.json",
        ),
        source_playlist_id="source-playlist",
    )
    repo = TransferRepository(database_path)
    review_decision = next(
        decision
        for decision in repo.load_match_decisions(result.transfer_run_id)
        if decision.status is MatchStatus.NEEDS_REVIEW
    )

    exit_code = main(
        [
            "review",
            "--db",
            str(database_path),
            "--run-id",
            result.transfer_run_id,
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

    exit_code = main(
        [
            "transfer",
            "--config",
            str(config_path),
            "--source-platform",
            "spotify",
            "--destination-platform",
            "mock",
            "--source-playlist",
            "source-playlist",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == "clear spotify auth error\n"
    assert "Traceback" not in captured.err


def test_transfer_uses_config_defaults_when_cli_args_are_omitted(tmp_path) -> None:
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
            "transfer": {
                "source_platform": "mock",
                "destination_platform": "mock",
                "source_playlist": "source-playlist",
                "dry_run": True,
                "restart": False,
                "output_dir": "reports/config-transfer",
            }
        },
    )

    exit_code = main(["transfer", "--config", str(config_path)])

    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert exit_code == 0
    assert run_id is not None
    report_dir = reports_path.parent / "reports" / "config-transfer" / run_id[:8]
    assert list(report_dir.glob("transfer-summary-*.json"))


def test_transfer_cli_args_override_config_defaults(tmp_path) -> None:
    config_path, database_path, _, _ = _config_file(
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
            "transfer": {
                "source_platform": "spotify",
                "destination_platform": "spotify",
                "source_playlist": "wrong-playlist",
                "dry_run": False,
                "restart": True,
            }
        },
    )

    exit_code = main(
        [
            "transfer",
            "--config",
            str(config_path),
            "--source-platform",
            "mock",
            "--destination-platform",
            "mock",
            "--source-playlist",
            "source-playlist",
            "--dry-run",
            "--no-restart",
        ]
    )

    repo = TransferRepository(database_path)
    assert exit_code == 0
    assert repo.find_run_id("mock|mock|source-playlist||dry-run") is not None


def test_execute_and_resume_skip_recorded_duplicate_destination_writes(tmp_path) -> None:
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
    dry_run = dry_run_mock_transfer(config, source_playlist_id="source-playlist")

    first_execute = execute_mock_transfer(
        config,
        transfer_run_id=dry_run.transfer_run_id,
        create_playlist_name="Copied",
    )
    resume = main(
        [
            "resume",
            "--config",
            str(config_path),
            "--run-id",
            dry_run.transfer_run_id,
        ]
    )

    writes = json.loads(writes_path.read_text(encoding="utf-8"))
    assert first_execute.attempted_count == 2
    assert resume == 0
    assert writes[first_execute.destination_playlist_id]["track_ids"] == [
        "dest-shared",
        "dest-shared",
    ]
    assert TransferRepository(database_path).load_metrics(
        dry_run.transfer_run_id
    ).write_success_count == 2


def test_execute_and_resume_use_configured_run_defaults(tmp_path) -> None:
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
    dry_run = dry_run_mock_transfer(config, source_playlist_id="source-playlist")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["commands"] = {
        "execute": {
            "run_id": dry_run.transfer_run_id,
            "create_playlist": "Copied",
        },
        "resume": {
            "run_id": dry_run.transfer_run_id,
        },
    }
    _write_json(config_path, payload)

    execute = main(["execute", "--config", str(config_path)])
    resume = main(["resume", "--config", str(config_path)])

    writes = json.loads(writes_path.read_text(encoding="utf-8"))
    assert execute == 0
    assert resume == 0
    assert list(writes.values())[0]["track_ids"] == ["dest-1"]


def test_execute_persists_supplied_destination_playlist_for_resume(tmp_path) -> None:
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
    dry_run = dry_run_mock_transfer(config, source_playlist_id="source-playlist")

    execute_mock_transfer(
        config,
        transfer_run_id=dry_run.transfer_run_id,
        destination_playlist_id="existing-playlist",
    )
    resume = main(
        [
            "resume",
            "--config",
            str(config_path),
            "--run-id",
            dry_run.transfer_run_id,
        ]
    )

    writes = json.loads(writes_path.read_text(encoding="utf-8"))
    assert resume == 0
    assert TransferRepository(database_path).load_run(
        dry_run.transfer_run_id
    ).destination_playlist_id == "existing-playlist"
    assert set(writes) == {"existing-playlist"}
    assert writes["existing-playlist"]["track_ids"] == ["dest-1"]


def test_export_reports_include_expected_columns_and_region_reason(tmp_path) -> None:
    config_path, database_path, _, reports_path = _phase4_fixture(tmp_path)
    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None

    exit_code = main(
        [
            "export-report",
            "--db",
            str(database_path),
            "--run-id",
            run_id,
            "--output-dir",
            str(reports_path),
            "--format",
            "both",
        ]
    )

    rows = build_unavailable_rows(repo, run_id)
    report_dir = reports_path / run_id[:8]
    csv_path = next(report_dir.glob("unavailable-tracks-*.csv"))
    json_path = next(report_dir.glob("unavailable-tracks-*.json"))
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
    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    review_decision = next(
        decision
        for decision in repo.load_match_decisions(run_id)
        if decision.status is MatchStatus.NEEDS_REVIEW
    )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["commands"] = {
        "review": {
            "run_id": run_id,
            "candidate_rank": 1,
        },
        "export_report": {
            "run_id": run_id,
            "output_dir": "reports/config-export",
            "format": "json",
        },
    }
    _write_json(config_path, payload)

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
    report_dir = reports_path.parent / "reports" / "config-export" / run_id[:8]
    assert list(report_dir.glob("unavailable-tracks-*.json"))


def test_export_reports_do_not_overwrite_same_second_snapshot(tmp_path, monkeypatch) -> None:
    config_path, database_path, _, reports_path = _phase4_fixture(tmp_path)
    main(["dry-run", "--config", str(config_path), "--source-playlist", "source-playlist"])
    repo = TransferRepository(database_path)
    run_id = repo.find_run_id("mock|mock|source-playlist||dry-run")
    assert run_id is not None
    monkeypatch.setattr(exports_module, "_short_timestamp", lambda: "143022")

    first = main(
        [
            "export-report",
            "--db",
            str(database_path),
            "--run-id",
            run_id,
            "--output-dir",
            str(reports_path),
            "--format",
            "json",
        ]
    )
    second = main(
        [
            "export-report",
            "--db",
            str(database_path),
            "--run-id",
            run_id,
            "--output-dir",
            str(reports_path),
            "--format",
            "json",
        ]
    )

    report_dir = reports_path / run_id[:8]
    assert first == 0
    assert second == 0
    assert (report_dir / "transfer-summary-143022.json").exists()
    assert (report_dir / "transfer-summary-143022-2.json").exists()
