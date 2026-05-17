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

The tracked `fixtures/` files allow a credential-free local workflow:

```powershell
uv run playlist-porter init-config --path cli-config.json
```

Set `commands.match.source_playlist` to `sample-mixed` in `cli-config.json`.
Set `commands.match.restart` to `true` when you want a fresh run, and set
`commands.write.create_playlist` to a local mock playlist name such as
`Sample Copy`.

Then run the workflow:

```powershell
uv run playlist-porter match --config cli-config.json
uv run playlist-porter review --config cli-config.json
uv run playlist-porter write --config cli-config.json
```

`match` writes the current run ID back to top-level `run_id`, so the later
commands use the same persisted run. Local state is written under `state/` and
reports under `reports/`.

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
