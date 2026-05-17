# Playlist Workflow

Playlist Porter uses the same lifecycle for real platforms and mock fixture
runs:

1. `match`: read a source playlist, search the destination catalog, score
   candidates, persist decisions, export reports, and save the run ID to config.
2. `review`: accept or reject uncertain matches in the persisted run.
3. `write`: write approved matches from that run to the destination.
4. `export-report`: regenerate persisted reports when needed.

The JSON examples below show the relevant `cli-config.json` sections to edit
after running `init-config`; they are not complete replacement config files.

## Spotify To QQ Music

Configure the shared workflow state and command defaults:

```json
{
  "database_path": "state/playlist-porter.sqlite",
  "report_output_dir": "reports/s-q",
  "report_format": "json",
  "run_id": "",
  "source_platform": "spotify",
  "destination_platform": "qqmusic",
  "commands": {
    "match": {
      "source_playlist": "<spotify-playlist-id-or-url>",
      "restart": true
    },
    "write": {
      "destination_playlist_id": "",
      "create_playlist": "Spotify Copy"
    }
  }
}
```

Then run each step with the config:

```powershell
uv run --env-file .env playlist-porter match --config cli-config.json
uv run playlist-porter review --config cli-config.json
uv run --env-file .env playlist-porter write --config cli-config.json
uv run playlist-porter export-report --config cli-config.json
```

After `match` succeeds, top-level `run_id` is updated in `cli-config.json`. The
later steps use that value automatically. Replace `commands.write.create_playlist`
with `commands.write.destination_playlist_id` to append to an existing QQ Music
songlist instead of creating a new one.

## QQ Music To Spotify

Configure the shared workflow state and command defaults:

```json
{
  "database_path": "state/playlist-porter.sqlite",
  "report_output_dir": "reports/q-s",
  "report_format": "json",
  "run_id": "",
  "source_platform": "qqmusic",
  "destination_platform": "spotify",
  "commands": {
    "match": {
      "source_playlist": "<qqmusic-songlist-id-or-url>",
      "restart": true
    },
    "write": {
      "destination_playlist_id": "",
      "create_playlist": "QQ Music Copy"
    }
  }
}
```

Then run each step with the config:

```powershell
uv run --env-file .env playlist-porter match --config cli-config.json
uv run playlist-porter review --config cli-config.json
uv run --env-file .env playlist-porter write --config cli-config.json
uv run playlist-porter export-report --config cli-config.json
```

After `match` succeeds, top-level `run_id` is updated in `cli-config.json`. The
later steps use that value automatically. Replace `commands.write.create_playlist`
with `commands.write.destination_playlist_id` to append to an existing Spotify
playlist instead of creating a new one.

## Mock Fixture Workflow

The repository includes credential-free fixtures so a checkout can exercise the
workflow without external services:

```powershell
uv run playlist-porter init-config --path cli-config.json
```

Then configure the mock source playlist and optional write target:

```json
{
  "database_path": "state/playlist-porter.sqlite",
  "report_output_dir": "reports/mock",
  "report_format": "json",
  "run_id": "",
  "source_platform": "mock",
  "destination_platform": "mock",
  "mock": {
    "source_playlists_path": "fixtures/mock-playlists.json",
    "destination_catalog_path": "fixtures/mock-catalog.json",
    "writes_path": "state/mock-writes.json"
  },
  "commands": {
    "match": {
      "source_playlist": "sample-mixed",
      "restart": true
    },
    "write": {
      "destination_playlist_id": "",
      "create_playlist": "Sample Copy"
    }
  }
}
```

Then run the mock lifecycle:

```powershell
uv run playlist-porter match --config cli-config.json
uv run playlist-porter review --config cli-config.json
uv run playlist-porter write --config cli-config.json
```

Mock writes record approved destination IDs to the configured mock writes file,
usually under `state/`.

## Config-Owned Workflow State

The transfer direction and state paths are intentionally config-owned:

- `database_path` is shared by every command.
- `report_output_dir` is the base report directory for every command.
- `report_format` controls all generated reports and accepts `json`, `csv`, or
  `both`; missing or empty values default to `json`.
- `run_id` identifies the current persisted run for `review`, `write`, and
  `export-report`.
- `source_platform` and `destination_platform` define the direction of the
  workflow.

`match` reads top-level platform direction plus `commands.match`, then writes
the resolved run ID back to top-level `run_id` after success. `review`, `write`,
and `export-report` load that persisted run and validate that it matches the
configured source and destination platforms.

CLI options are reserved for command selection, logging, config file selection,
`init-config` file management, and immediate review actions. Review actions can
be supplied with:

```powershell
uv run playlist-porter review --config cli-config.json --source-track-id <track-id> --action accept --candidate-rank 1
```

## Verbosity And Debug Logs

Logging is flag-controlled for each command. These options are not read from
`cli-config.json`:

- `-v`: print INFO and above to the console.
- `-vv`: print DEBUG and above to the console.
- `-l` / `--log`: write DEBUG diagnostics to a timestamped file under `logs/`.

The flags work before or after the subcommand:

```powershell
uv run playlist-porter -v match --config cli-config.json
uv run playlist-porter match --config cli-config.json -vv --log
```

## Playlist Identifiers

`commands.match.source_playlist` identifies the playlist to read from
`source_platform`.

Current source playlist support:

- Spotify accepts raw playlist IDs, open.spotify.com playlist URLs, and
  `spotify:playlist:<id>` URIs.
- QQ Music accepts raw numeric songlist IDs and common playlist URL forms where
  a numeric ID can be extracted.

`commands.write.destination_playlist_id` identifies an existing normal playlist
or songlist on `destination_platform`.

Current destination playlist support:

- Spotify accepts raw playlist IDs, open.spotify.com playlist URLs, and
  `spotify:playlist:<id>` URIs. These forms normalize to the canonical playlist
  ID before the write target is recorded or compared with a persisted run.
- QQ Music accepts public numeric songlist IDs and common playlist URL forms.
  Existing targets are resolved during validation and stored as the internal
  write target needed by QQ Music.

## Create Versus Existing Destination

`commands.write.destination_playlist_id` and `commands.write.create_playlist`
are mutually exclusive write target paths:

- Use `destination_playlist_id` to append approved matches to an existing
  normal playlist or songlist.
- Use `create_playlist` to create a new normal playlist or songlist by name
  before writing.

`create_playlist` is a creation request, not an existing-playlist lookup or
deduplication request. If a playlist with the same name already exists, the
platform may create another playlist with that name.

Non-playlist targets such as Spotify Liked Songs are separate from normal
playlist IDs and are not currently supported write targets.

## Reports

`match` and `write` export reports automatically. To regenerate reports for an
existing run:

```powershell
uv run playlist-porter export-report --config cli-config.json
```

Reports are grouped under `report_output_dir/<short-run-id>/`, where
`<short-run-id>` is the first eight characters of the persisted run ID.

Report filenames include the command that produced them:

- `summary-<HHMMSS>-<command>.<ext>`
- `unavailable-<HHMMSS>-<command>.<ext>`

If a report with the same timestamp already exists, a numeric suffix is added,
for example `summary-143022-match-2.json`.
