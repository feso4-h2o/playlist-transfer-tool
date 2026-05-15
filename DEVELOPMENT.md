# Development

This project uses Python 3.12 and `uv`.

## Setup

```powershell
uv sync
uv run playlist-porter --version
```

## Quality Checks

Run the full test suite:

```powershell
uv run pytest
```

Run linting:

```powershell
uv run ruff check .
```

Apply safe Ruff fixes:

```powershell
uv run ruff check . --fix
```

## Mock Workflow Smoke Test

The tracked `fixtures/` files allow a credential-free local matching preview:

```powershell
uv run playlist-porter init-config --path cli-config.json
uv run playlist-porter match --config cli-config.json --source-platform mock --destination-platform mock --source-playlist sample-mixed --restart
```

To exercise the mock write path after reviewing a run, use the run id printed
by `match`:

```powershell
uv run playlist-porter review --config cli-config.json --run-id <run-id>
uv run playlist-porter write --config cli-config.json --destination-platform mock --run-id <run-id> --create-playlist "Sample Copy"
```

Local state is written under `state/` and reports under `reports/`.

## VCR Cassettes

Unit tests must not require live network access. If you add or update VCR.py
cassettes for Spotify or QQ Music read-only calls:

- Require explicit opt-in environment variables such as
  `RUN_LIVE_SPOTIFY_TESTS=1` or `RUN_LIVE_QQMUSIC_TESTS=1`.
- Scrub OAuth tokens, cookies, usernames, private playlist IDs, and sensitive
  headers before committing cassettes.
- Use small, intentional fixture playlists.
- Do not record live write operations by default.

## Branch And PR Checks

Before opening a PR, run:

```powershell
uv run ruff check .
uv run pytest
```

PR descriptions should summarize behavior changes and list validation commands
that were run.
