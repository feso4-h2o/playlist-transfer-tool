from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path

import anyio


def _load_helper_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "create_qqmusic_credential.py"
    spec = importlib.util.spec_from_file_location("create_qqmusic_credential", script_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load helper script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = _load_helper_module()


def test_qqmusic_login_helper_requires_risk_acknowledgement(tmp_path, capsys) -> None:
    args = Namespace(
        output=str(tmp_path / "credential.json"),
        qr_dir=str(tmp_path),
        login_type="qq",
        timeout_seconds=180.0,
        force=False,
        acknowledge_risk=False,
    )

    exit_code = anyio.run(helper.create_credential, args)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "QQ MUSIC CREDENTIAL RISK NOTICE" in captured.out
    assert "reusable QQ Music session credential" in captured.out
    assert "Refusing to run without --acknowledge-risk." in captured.out


def test_qqmusic_login_helper_refuses_to_overwrite_existing_file(tmp_path, capsys) -> None:
    output_path = tmp_path / "credential.json"
    output_path.write_text("existing", encoding="utf-8")
    args = Namespace(
        output=str(output_path),
        qr_dir=str(tmp_path),
        login_type="qq",
        timeout_seconds=180.0,
        force=False,
        acknowledge_risk=True,
    )

    exit_code = anyio.run(helper.create_credential, args)

    assert exit_code == 2
    assert output_path.read_text(encoding="utf-8") == "existing"
    assert "Refusing to overwrite existing credential file" in capsys.readouterr().out


def test_qqmusic_login_helper_parser_requires_output() -> None:
    parser = helper.build_parser()

    try:
        parser.parse_args(["--acknowledge-risk"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected parser to reject missing --output")


def test_qqmusic_login_helper_defaults_qr_dir_to_current_folder(tmp_path) -> None:
    parser = helper.build_parser()

    args = parser.parse_args(
        [
            "--output",
            str(tmp_path / "credential.json"),
            "--acknowledge-risk",
        ]
    )

    assert args.qr_dir == "."
