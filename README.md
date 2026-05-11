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

- Local mock dry-runs and writes from JSON fixtures.
- Direction-aware `mock`, `spotify`, and `qqmusic` transfer orchestration.
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
uv run playlist-porter init-config --path playlist-porter.json
```

The generated `playlist-porter.json` points at the tracked sample mock fixtures
under `fixtures/`, stores SQLite state under `state/`, and writes reports under
`reports/`. Those local runtime outputs are ignored by Git.

Copy `.env.example` to your local environment manager or shell profile and set
the values there. Credentials are read from the process environment, not from
`playlist-porter.json`. For local runs, prefer loading `.env` through `uv`:

```powershell
uv run --env-file .env playlist-porter transfer --config playlist-porter.json
```

Spotify fields:

- `SPOTIFY_CLIENT_ID`: Spotify app client ID.
- `SPOTIFY_CLIENT_SECRET`: Spotify app client secret.
- `SPOTIFY_REDIRECT_URI`: redirect URI registered on the Spotify app, for
  example `http://127.0.0.1:8080/callback`.
- `SPOTIFY_SCOPES`: optional space-delimited override for playlist read/write
  scopes.

QQ Music fields:

- `QQMUSIC_CREDENTIAL_PATH`: path to a local JSON file containing credential
  data accepted by `qqmusic_api.Credential`.
- `QQMUSIC_USER_ID`: optional account/user identifier if your QQ Music workflow
  needs it.

Do not commit OAuth tokens, QQ Music cookies, credential JSON, SQLite databases,
or generated reports.

The generated config also includes optional `commands` defaults. Values in that
section let you shorten repeated commands, and explicit CLI arguments still
override the configured defaults.

## Mock Dry-Run

The repository includes credential-free fixtures so a new checkout can exercise
the workflow without external services:

```powershell
uv run playlist-porter init-config --path playlist-porter.json
uv run playlist-porter dry-run --config playlist-porter.json --source-playlist sample-mixed
```

The command prints a run ID and records match decisions in
`state/playlist-porter.sqlite`.

You can run the newer direction-aware command against the same mock data:

```powershell
uv run playlist-porter transfer --config playlist-porter.json --source-platform mock --destination-platform mock --source-playlist sample-mixed --dry-run
```

If those values are configured under `commands.transfer`, this can be shortened
to:

```powershell
uv run playlist-porter transfer --config playlist-porter.json
```

## Review

Review uncertain matches after a dry-run:

```powershell
uv run playlist-porter review --db state/playlist-porter.sqlite --run-id <run-id>
```

If `commands.review` supplies the database path and run ID:

```powershell
uv run playlist-porter review --config playlist-porter.json
```

For non-interactive updates, pass a source track UUID from the review output:

```powershell
uv run playlist-porter review --db state/playlist-porter.sqlite --run-id <run-id> --source-track-id <source-track-id> --action accept --candidate-rank 1
```

Accepted review overrides are stored in SQLite and reused when executing the
same run.

## Execute

Mock write execution writes approved destination IDs to `state/mock-writes.json`:

```powershell
uv run playlist-porter execute --config playlist-porter.json --run-id <run-id> --create-playlist "Sample Copy"
```

For real platform writes, execute a reviewed dry-run with the `transfer` command:

```powershell
uv run playlist-porter transfer --config playlist-porter.json --destination-platform spotify --run-id <run-id> --write --create-playlist "QQ Music Copy"
```

Only `isrc_exact`, `metadata_high_confidence`, and user-approved matches are
written. Medium-confidence, needs-review, rejected, and not-found tracks are
skipped.

Resume an interrupted mock write:

```powershell
uv run playlist-porter resume --config playlist-porter.json --run-id <run-id>
```

Resume a real destination write by rerunning the `transfer --run-id ... --write`
command with the same destination platform and playlist target.

## Reports

Dry-runs and direction-aware transfers export deterministic reports to the
configured report directory:

- `transfer-summary.json` and `transfer-summary.csv`: aggregate metrics from
  persisted transfer state.
- `unavailable-tracks.json` and `unavailable-tracks.csv`: not-found,
  unresolved, and rejected tracks with attempted queries, top alternates,
  confidence scores, and reason codes.

Export reports again for an existing run:

```powershell
uv run playlist-porter export-report --db state/playlist-porter.sqlite --run-id <run-id> --output-dir reports --format both
```

If `commands.export_report` supplies the database path, run ID, output
directory, and format:

```powershell
uv run playlist-porter export-report --config playlist-porter.json
```

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for test, lint, mock transfer, and VCR
cassette guidance. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common user
and developer failures.
