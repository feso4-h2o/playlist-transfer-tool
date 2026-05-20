# Playlist Transfer Tool

Playlist Transfer Tool is a CLI-first Python package for one-way playlist
transfers between Spotify and QQ Music. It fetches a source playlist, searches
the destination catalog, scores metadata-first matches, lets you review uncertain
tracks, writes only approved matches, and exports the transfer outcome.

The MVP is focused on Spotify <-> QQ Music personal playlist portability. It is
not a continuous sync service, catalog scraper, hosted SaaS, or commercial
distribution.

## Status

The current CLI supports:

- Direction-aware `mock`, `spotify`, and `qqmusic` transfer orchestration.
- Local mock matching and writes from JSON fixtures.
- SQLite transfer state, resumable writes, review overrides, and reports.
- Spotify OAuth through Spotipy.
- QQ Music access through the unofficial `qqmusic-api-python` package.

QQ Music integration depends on reverse-engineered APIs and may break when QQ
Music changes its behavior. Use this tool only with accounts and playlists you
are allowed to access.

## License

This project is licensed under GPL-3.0-or-later. QQ Music support relies on a
GPL-compatible dependency, so source availability and GPL obligations matter for
any redistribution.

## Requirements

- Python 3.12
- `uv`
- Spotify developer credentials for Spotify transfers
- Local QQ Music credential JSON for QQ Music transfers

Install dependencies:

```powershell
uv sync
```

Verify the CLI:

```powershell
uv run playlist-porter --version
```

## Configuration

Write a starter config:

```powershell
uv run playlist-porter init-config --path cli-config.json
```

The generated `cli-config.json` stores local paths, workflow state, platform
behavior flags, mock fixture paths, and the small set of command defaults used
by the transfer lifecycle. Spotify and QQ Music credentials are read from the
process environment, not from `cli-config.json`.

For local runs that need credentials, prefer loading them with `uv`:

```powershell
uv run --env-file .env playlist-porter match --config cli-config.json
```

See [docs/configuration.md](docs/configuration.md) for Spotify Developer
Dashboard setup, `.env` values, and the optional QQ Music credential helper.

Do not commit OAuth tokens, QQ Music cookies, credential JSON, SQLite databases,
or generated reports.

## Transfer Workflow

Use the same lifecycle for Spotify, QQ Music, and mock fixture runs:

1. `match`: read source tracks, search the destination, score candidates,
   persist decisions, and export reports.
2. `review`: accept or reject uncertain matches from a persisted run.
3. `write`: write approved matches from the reviewed run to the destination.
4. `export-report`: regenerate reports for an existing run.

With workflow state configured in `cli-config.json`, the lifecycle uses short
commands. After `match` succeeds, the CLI writes the resolved run ID back to
top-level `run_id` so the later steps naturally continue the same run. If
Spotify credentials are stored in `.env`, load that file for `match` as well as
`write`:

```powershell
uv run --env-file .env playlist-porter match --config cli-config.json
uv run playlist-porter review --config cli-config.json
uv run --env-file .env playlist-porter write --config cli-config.json
uv run playlist-porter export-report --config cli-config.json
```

See [docs/playlist-workflow.md](docs/playlist-workflow.md) for Spotify -> QQ
Music, QQ Music -> Spotify, mock fixture examples, `cli-config.json`
examples, playlist identifier support, write-target choices, and report export
details. Write targets can be normal playlists/songlists or account library
targets such as Spotify Liked Songs and QQ Music “我喜欢”.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for test, lint, mock transfer, and VCR
cassette guidance. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common user
and developer failures.
