import importlib.util
import sqlite3
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).parents[2] / "scripts" / "reset_write_status.py"
    spec = importlib.util.spec_from_file_location("reset_write_status", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            create table transfer_runs (
                internal_id text primary key,
                source_platform text not null,
                destination_platform text not null,
                destination_playlist_id text
            );
            create table transfer_steps (
                id integer primary key autoincrement,
                transfer_run_id text not null,
                step_type text not null,
                source_track_internal_id text,
                destination_track_id text,
                status text not null
            );
            create table transfer_metrics (
                transfer_run_id text primary key,
                write_success_count integer not null
            );
            insert into transfer_runs values (
                'run-1', 'mock', 'qqmusic', '35:9712240561'
            );
            insert into transfer_runs values (
                'run-2', 'mock', 'spotify', 'spotify-playlist'
            );
            insert into transfer_steps (
                transfer_run_id, step_type, source_track_internal_id, destination_track_id, status
            ) values
                ('run-1', 'write_track', 'source-1', 'dest-1', 'completed'),
                ('run-1', 'write_track', 'source-2', 'dest-2', 'failed'),
                ('run-1', 'other_step', 'source-3', 'dest-3', 'completed'),
                ('run-2', 'write_track', 'source-4', 'dest-4', 'completed');
            insert into transfer_metrics values ('run-1', 1);
            insert into transfer_metrics values ('run-2', 1);
            """
        )


def _count_rows(path: Path, table: str, where: str = "1 = 1") -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(f"select count(*) from {table} where {where}").fetchone()[0])


def test_reset_write_status_dry_run_does_not_delete_rows(tmp_path, capsys) -> None:
    module = _load_script_module()
    database_path = tmp_path / "state.sqlite"
    _create_database(database_path)

    exit_code = module.main(["--database", str(database_path), "--run-id", "run-1"])

    assert exit_code == 0
    assert "dry run only" in capsys.readouterr().out
    assert _count_rows(database_path, "transfer_steps") == 4
    assert _count_rows(database_path, "transfer_metrics") == 2


def test_reset_write_status_deletes_only_selected_run_write_markers(tmp_path) -> None:
    module = _load_script_module()
    database_path = tmp_path / "state.sqlite"
    _create_database(database_path)

    exit_code = module.main(
        [
            "--database",
            str(database_path),
            "--run-id",
            "run-1",
            "--yes",
            "--no-backup",
        ]
    )

    assert exit_code == 0
    assert _count_rows(database_path, "transfer_steps", "transfer_run_id = 'run-1'") == 1
    assert _count_rows(database_path, "transfer_steps", "transfer_run_id = 'run-2'") == 1
    assert _count_rows(database_path, "transfer_metrics", "transfer_run_id = 'run-1'") == 0
    assert _count_rows(database_path, "transfer_metrics", "transfer_run_id = 'run-2'") == 1


def test_reset_write_status_returns_error_for_unknown_run(tmp_path, capsys) -> None:
    module = _load_script_module()
    database_path = tmp_path / "state.sqlite"
    _create_database(database_path)

    exit_code = module.main(["--database", str(database_path), "--run-id", "missing"])

    assert exit_code == 2
    assert "transfer run not found: missing" in capsys.readouterr().err
