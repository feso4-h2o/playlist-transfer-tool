# Playlist Workflow

Playlist Porter uses the same lifecycle for real platforms and mock fixture
runs:

1. `match`: read a source playlist, search the destination catalog, score
   candidates, persist decisions, and export reports.
2. `review`: accept or reject uncertain matches in the persisted run.
3. `write`: write approved matches from that run to the destination.
4. `export-report`: regenerate persisted reports when needed.

The JSON examples below show the relevant `playlist-porter.json` sections to
edit after running `init-config`; they are not complete replacement config
files.

## Spotify To QQ Music

Configure the source and destination defaults:

```json
{
  "commands": {
    "match": {
      "source_platform": "spotify",
      "destination_platform": "qqmusic",
      "source_playlist": "<spotify-playlist-id-or-url>",
      "restart": true,
      "output_dir": "reports"
    },
    "review": {
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>"
    },
    "write": {
      "destination_platform": "qqmusic",
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>",
      "output_dir": "reports",
      "destination_playlist_id": "",
      "create_playlist": "Spotify Copy"
    },
    "export_report": {
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>",
      "output_dir": "reports",
      "format": "both"
    }
  }
}
```

Then run each step with the config:

```powershell
uv run --env-file .env playlist-porter match --config playlist-porter.json
```

After `match` prints the run ID, paste it into `commands.review.run_id`,
`commands.write.run_id`, and `commands.export_report.run_id`. Then run:

```powershell
uv run playlist-porter review --config playlist-porter.json
uv run --env-file .env playlist-porter write --config playlist-porter.json
uv run playlist-porter export-report --config playlist-porter.json
```

If you do not want to edit the config between steps, pass `--run-id <run-id>` to
`review`, `write`, and `export-report`; CLI flags override configured defaults.

Replace `write.create_playlist` with `write.destination_playlist_id` to append
to an existing QQ Music songlist instead of creating a new one.

## QQ Music To Spotify

Configure the source and destination defaults:

```json
{
  "commands": {
    "match": {
      "source_platform": "qqmusic",
      "destination_platform": "spotify",
      "source_playlist": "<qqmusic-songlist-id-or-url>",
      "restart": true,
      "output_dir": "reports"
    },
    "review": {
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>"
    },
    "write": {
      "destination_platform": "spotify",
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>",
      "output_dir": "reports",
      "destination_playlist_id": "",
      "create_playlist": "QQ Music Copy"
    },
    "export_report": {
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>",
      "output_dir": "reports",
      "format": "both"
    }
  }
}
```

Then run each step with the config:

```powershell
uv run --env-file .env playlist-porter match --config playlist-porter.json
```

After `match` prints the run ID, paste it into `commands.review.run_id`,
`commands.write.run_id`, and `commands.export_report.run_id`. Then run:

```powershell
uv run playlist-porter review --config playlist-porter.json
uv run --env-file .env playlist-porter write --config playlist-porter.json
uv run playlist-porter export-report --config playlist-porter.json
```

If you do not want to edit the config between steps, pass `--run-id <run-id>` to
`review`, `write`, and `export-report`; CLI flags override configured defaults.

Replace `write.create_playlist` with `write.destination_playlist_id` to append
to an existing Spotify playlist instead of creating a new one.

## Mock Fixture Workflow

The repository includes credential-free fixtures so a checkout can exercise the
workflow without external services:

```powershell
uv run playlist-porter init-config --path playlist-porter.json
```

Then configure mock defaults:

```json
{
  "mock": {
    "source_playlists_path": "fixtures/mock-playlists.json",
    "destination_catalog_path": "fixtures/mock-catalog.json",
    "writes_path": "state/mock-writes.json"
  },
  "commands": {
    "match": {
      "source_platform": "mock",
      "destination_platform": "mock",
      "source_playlist": "sample-mixed",
      "restart": true,
      "output_dir": "reports"
    },
    "review": {
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>"
    },
    "write": {
      "destination_platform": "mock",
      "database_path": "state/playlist-porter.sqlite",
      "run_id": "<run-id>",
      "output_dir": "reports",
      "destination_playlist_id": "",
      "create_playlist": "Sample Copy"
    }
  }
}
```

Then run the mock lifecycle:

```powershell
uv run playlist-porter match --config playlist-porter.json
```

After `match` prints the run ID, paste it into `commands.review.run_id` and
`commands.write.run_id`. Then run:

```powershell
uv run playlist-porter review --config playlist-porter.json
uv run playlist-porter write --config playlist-porter.json
```

If you do not want to edit the config between steps, pass `--run-id <run-id>` to
`review` and `write`; CLI flags override configured defaults.

Mock writes record approved destination IDs to the configured mock writes file,
usually under `state/`. Match and write reports are written under the configured
report output directory, usually `reports/`.

## Config Defaults And Overrides

Explicit CLI flags override defaults from `commands.*` in `playlist-porter.json`.

`match` overrides:

- `--source-platform`: `commands.match.source_platform`
- `--destination-platform`: `commands.match.destination_platform`
- `--source-playlist`: `commands.match.source_playlist`
- `--restart` / `--no-restart`: `commands.match.restart`
- `--db`: `commands.match.database_path`
- `--output-dir`: `commands.match.output_dir`

`review` overrides:

- `--db`: `commands.review.database_path`
- `--run-id`: `commands.review.run_id`

`write` overrides:

- `--destination-platform`: `commands.write.destination_platform`
- `--db`: `commands.write.database_path`
- `--run-id`: `commands.write.run_id`
- `--output-dir`: `commands.write.output_dir`
- `--destination-playlist-id`: `commands.write.destination_playlist_id`
- `--create-playlist`: `commands.write.create_playlist`

`export-report` overrides:

- `--db`: `commands.export_report.database_path`
- `--run-id`: `commands.export_report.run_id`
- `--output-dir`: `commands.export_report.output_dir`
- `--format`: `commands.export_report.format`

For example, this command keeps the configured platforms and output directory,
but uses a different source playlist and restart setting for one run:

```powershell
uv run playlist-porter match --config playlist-porter.json --source-playlist <playlist-id-or-url> --restart
```

## Playlist Identifiers

`match --source-playlist` identifies the playlist to read from
`--source-platform`. The same value can be stored in
`commands.match.source_playlist`.

Current source playlist support:

- Spotify accepts raw playlist IDs, open.spotify.com playlist URLs, and
  `spotify:playlist:<id>` URIs.
- QQ Music accepts raw numeric songlist IDs and common playlist URL forms where
  a numeric ID can be extracted.

`write --destination-playlist-id` identifies an existing normal playlist or
songlist on `--destination-platform`. The same value can be stored in
`commands.write.destination_playlist_id`.

Current destination playlist support:

- Spotify destination playlist IDs should be raw Spotify playlist IDs.
- QQ Music destination songlist IDs should be raw numeric songlist IDs.

Destination URL parsing is not currently supported for writes.

## Create Versus Existing Destination

`write --destination-playlist-id` and `write --create-playlist` are separate
write target paths and should be treated as mutually exclusive options. Set
exactly one for a write, either through CLI flags or `commands.write` defaults:

- Use `--destination-playlist-id` to append approved matches to an existing
  normal playlist or songlist.
- Use `--create-playlist` to create a new normal playlist or songlist by name
  before writing.

`--create-playlist` is a creation request, not an existing-playlist lookup or
deduplication request. If a playlist with the same name already exists, the
platform may create another playlist with that name.

Non-playlist targets such as Spotify Liked Songs are separate from normal
playlist IDs and are not currently supported write targets.

## Reports

`match` and `write` export reports automatically. To regenerate reports for an
existing run:

```powershell
uv run playlist-porter export-report --config playlist-porter.json --run-id <run-id> --output-dir reports --format both
```

Reports are grouped by short run ID and include transfer summaries plus
unavailable, unresolved, and rejected tracks.
