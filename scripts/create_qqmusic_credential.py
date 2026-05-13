"""Generate a local QQ Music credential JSON through qqmusic-api-python login."""

from __future__ import annotations

import argparse
from pathlib import Path

import anyio
from qqmusic_api import Client
from qqmusic_api.models.login import QRLoginType
from qqmusic_api.modules.login_utils import QRCodeLoginSession
from rich.console import Console
from rich.panel import Panel

RISK_NOTICE = """\
- This uses the unofficial, reverse-engineered qqmusic-api-python login flow.
- The generated JSON is a reusable QQ Music session credential.
- Anyone who can read the JSON may be able to act as your QQ Music session.
- Prefer a dedicated low-value QQ Music account for testing.
- Store the output outside Git, do not sync it, and rotate/revoke it if exposed.

The main playlist-porter CLI does not run this login flow automatically.
"""

LOGIN_TYPES = {
    "qq": QRLoginType.QQ,
    "wechat": QRLoginType.WX,
    "mobile": QRLoginType.MOBILE,
}

console = Console()


def build_parser() -> argparse.ArgumentParser:
    """Build the helper parser."""

    parser = argparse.ArgumentParser(
        description="Generate a local QQ Music credential JSON for playlist-porter.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the credential JSON, for example state/qqmusic-credential.json.",
    )
    parser.add_argument(
        "--qr-dir",
        default=".",
        help="Directory where the QR code image will be saved. Defaults to the current folder.",
    )
    parser.add_argument(
        "--login-type",
        choices=sorted(LOGIN_TYPES),
        default="qq",
        help="QQ Music login flow to use.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for QR confirmation.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    parser.add_argument(
        "--acknowledge-risk",
        action="store_true",
        help="Required. Confirms you understand the credential and unofficial API risks.",
    )
    return parser


async def create_credential(args: argparse.Namespace) -> int:
    """Run the QR login flow and write the resulting credential JSON."""

    console.print(
        Panel.fit(
            RISK_NOTICE,
            title="[bold white on red] QQ MUSIC CREDENTIAL RISK NOTICE [/]",
            border_style="bold red",
            padding=(1, 2),
        )
    )
    if not args.acknowledge_risk:
        console.print("[bold red]Refusing to run without --acknowledge-risk.[/]")
        return 2

    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        console.print(f"[bold red]Refusing to overwrite existing credential file:[/] {output_path}")
        console.print("Pass --force only if you intend to replace it.")
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    qr_dir = Path(args.qr_dir)
    qr_dir.mkdir(parents=True, exist_ok=True)

    async with Client() as client:
        session = QRCodeLoginSession(
            client.login,
            LOGIN_TYPES[args.login_type],
            timeout_seconds=args.timeout_seconds,
        )
        qr = await session.get_qrcode()
        qr_path = qr.save(qr_dir)
        console.print(f"Scan this QR code to authorize QQ Music access: {qr_path}")
        console.print("Waiting for confirmation...")

        credential = await session.wait_qrcode_login()

    output_path.write_text(
        credential.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    console.print(f"Wrote QQ Music credential JSON: {output_path.resolve()}")
    console.print(f"Detected musicid: {credential.musicid}")
    console.print("Set QQMUSIC_CREDENTIAL_PATH to that file path when running playlist-porter.")
    return 0


def main() -> int:
    """Run the helper."""

    return anyio.run(create_credential, build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
