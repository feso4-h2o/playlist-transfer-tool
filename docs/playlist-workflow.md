# Playlist Workflow

Playlist Porter uses the same lifecycle for real platforms and mock fixture
runs:

1. `match`: read a source playlist, search the destination catalog, score
   candidates, persist decisions, and export reports.
2. `review`: accept or reject uncertain matches in the persisted run.
3. `write`: write approved matches from that run to the destination.
4. `export-report`: regenerate persisted reports when needed.

## Spotify To QQ Music

Match a Spotify source playlist against QQ Music:

```powershell
uv run --env-file .env playlist-porter match --config playlist-porter.json --source-platform spotify --destination-platform qqmusic --source-playlist <spotify-playlist-id-or-url> --restart
```

Review uncertain matches:

```powershell
uv run playlist-porter review --config playlist-porter.json --run-id <run-id>
```

Create a new QQ Music songlist and write approved matches:

```powershell
uv run --env-file .env playlist-porter write --config playlist-porter.json --destination-platform qqmusic --run-id <run-id> --create-playlist "Spotify Copy"
```

Or append to an existing QQ Music songlist by raw numeric ID:

```powershell
uv run --env-file .env playlist-porter write --config playlist-porter.json --destination-platform qqmusic --run-id <run-id> --destination-playlist-id <qqmusic-songlist-id>
```

## QQ Music To Spotify

Match a QQ Music source songlist against Spotify:

```powershell
uv run --env-file .env playlist-porter match --config playlist-porter.json --source-platform qqmusic --destination-platform spotify --source-playlist <qqmusic-songlist-id-or-url> --restart
```

Review uncertain matches:

```powershell
uv run playlist-porter review --config playlist-porter.json --run-id <run-id>
```

Create a new Spotify playlist and write approved matches:

```powershell
uv run --env-file .env playlist-porter write --config playlist-porter.json --destination-platform spotify --run-id <run-id> --create-playlist "QQ Music Copy"
```

Or append to an existing Spotify playlist by raw playlist ID:

```powershell
uv run --env-file .env playlist-porter write --config playlist-porter.json --destination-platform spotify --run-id <run-id> --destination-playlist-id <spotify-playlist-id>
```

## Mock Fixture Workflow

The repository includes credential-free fixtures so a checkout can exercise the
workflow without external services:

```powershell
uv run playlist-porter init-config --path playlist-porter.json
uv run playlist-porter match --config playlist-porter.json --source-platform mock --destination-platform mock --source-playlist sample-mixed --restart
uv run playlist-porter review --config playlist-porter.json --run-id <run-id>
uv run playlist-porter write --config playlist-porter.json --destination-platform mock --run-id <run-id> --create-playlist "Sample Copy"
```

Mock writes record approved destination IDs to the configured mock writes file,
usually under `state/`. Match and write reports are written under the configured
report output directory, usually `reports/`.

## Config Defaults And Overrides

The generated config includes optional defaults under `commands`:

- `commands.match.source_platform`
- `commands.match.destination_platform`
- `commands.match.source_playlist`
- `commands.match.restart`
- `commands.match.database_path`
- `commands.match.output_dir`
- `commands.review.database_path`
- `commands.review.run_id`
- `commands.write.destination_platform`
- `commands.write.database_path`
- `commands.write.run_id`
- `commands.write.output_dir`
- `commands.write.destination_playlist_id`
- `commands.write.create_playlist`
- `commands.export_report.database_path`
- `commands.export_report.run_id`
- `commands.export_report.output_dir`
- `commands.export_report.format`

Explicit CLI flags override these defaults. For example, if
`commands.match.source_platform`, `commands.match.destination_platform`, and
`commands.match.source_playlist` are already configured, this is enough:

```powershell
uv run --env-file .env playlist-porter match --config playlist-porter.json
```

You can override just the source playlist for one run:

```powershell
uv run --env-file .env playlist-porter match --config playlist-porter.json --source-playlist <playlist-id-or-url> --restart
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
write target paths:

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
