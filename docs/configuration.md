# Configuration

Playlist Porter separates local workflow configuration from credentials.

`cli-config.json` owns local paths, workflow identity, platform direction, mock
fixture paths, platform behavior flags, and command defaults. Spotify and QQ
Music credentials are environment-only inputs so secrets do not end up in
project config.

## Credential Scope

Mock fixture workflows do not need Spotify or QQ Music credentials.

Spotify playlist reads, Spotify searches, playlist writes, and Liked Songs
writes require OAuth environment variables because the Spotify adapter uses an
authenticated Spotipy client. This means `match` needs Spotify credentials
whenever Spotify is the source or destination platform.

QQ Music public playlist reads and destination searches can run without
`QQMUSIC_CREDENTIAL_PATH` when `qqmusic.allow_anonymous_read` is enabled in
`cli-config.json`. QQ Music writes always require a local credential JSON.

## Starter Config

Create a local config:

```powershell
uv run playlist-porter init-config --path cli-config.json
```

The generated file includes top-level workflow state:

- `database_path`: SQLite transfer state path shared by all commands.
- `report_output_dir`: base directory for generated reports.
- `report_format`: `json`, `csv`, or `both`; missing or empty values default to
  `json`.
- `run_id`: the transfer run used by `review`, `write`, and `export-report`.
  A successful `match` updates this value automatically.
- `source_platform` and `destination_platform`: the transfer direction used
  throughout the run.

It also includes platform and command sections:

- `mock`: fixture and mock-write paths.
- `spotify`: local Spotify behavior such as token cache path and playlist
  visibility for created playlists.
- `qqmusic`: QQ Music adapter behavior flags and page size.
- `commands.match`: `source_playlist` and `restart`.
- `commands.write`: `destination_target_type`, `destination_playlist_id`, and
  `create_playlist`.

Config owns workflow identity and shared state paths. CLI options are reserved
for command selection, logging, config file selection, `init-config` file
management, and immediate review actions.

## Environment Loading

Copy `.env.example` to `.env` or another local environment manager. The CLI does
not auto-load `.env`; use `uv run --env-file` for local runs that need
environment credentials:

```powershell
uv run --env-file .env playlist-porter match --config cli-config.json
```

Keep `.env`, OAuth token caches, QQ Music credential JSON, SQLite databases, and
generated reports out of Git.

## Spotify Setup

Create a Spotify app:

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. Create an app, for example named `playlist-transfer-tool`.
3. Add a local redirect URI such as `http://127.0.0.1:8888/callback`.
4. Select the Web API.
5. Open the created app, go to Settings, copy the Client ID, and reveal/copy the
   Client Secret.

Set local environment values:

```powershell
SPOTIFY_CLIENT_ID=<client-id>
SPOTIFY_CLIENT_SECRET=<client-secret>
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_SCOPES="playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public"
```

`SPOTIFY_SCOPES` is optional. Quote it in `.env` because scope values contain
spaces. If omitted, the CLI uses the default playlist read/write and Liked
Songs scopes. If your token cache was created before the `user-library-read`
and `user-library-modify` scopes were added, delete or refresh the cache and
authorize Spotify again. For Spotify Liked Songs writes, include
`user-library-read user-library-modify`.

The first Spotify command that needs OAuth may ask you to open a browser URL and
paste the redirected callback URL. Paste the full URL, including the `?code=...`
query string.

Example prompt and input shape:

```text
Go to the following URL: https://accounts.spotify.com/authorize?client_id=<client-id>&response_type=code&redirect_uri=http%3A%2F%2F127.0.0.1%3A8888%2Fcallback&scope=<scopes>
Enter the URL you were redirected to: http://127.0.0.1:8888/callback?code=<authorization-code>&state=<state>
```

The input is the local callback URL from the browser address bar after approving
the Spotify request, not the `accounts.spotify.com/authorize` URL.

## QQ Music Setup

QQ Music credentials are also environment-only:

```powershell
QQMUSIC_CREDENTIAL_PATH=<path-to-local-credential-json>
QQMUSIC_USER_ID=<optional-user-id>
```

`QQMUSIC_CREDENTIAL_PATH` should point to a local JSON credential accepted by
`qqmusic_api.Credential`. `QQMUSIC_USER_ID` is optional and only needed for
local workflows that require an explicit account identifier.

The `qqmusic.page_size` config controls how many songs are requested per page
when reading large QQ Music songlists for duplicate checks. The generated
default is conservative; larger values such as `1000` can reduce duplicate-check
latency for large destination songlists when QQ Music accepts the request.

### Optional Credential Helper

QQ Music writes require a local session credential. The main CLI does not create
or refresh that credential automatically. If you choose to use
`qqmusic-api-python`'s unofficial QR login flow, this repository includes a
helper. Read the helper's options first:

```powershell
uv run python scripts/create_qqmusic_credential.py --help
```

The helper:

- Prints a risk notice before login.
- Saves a QR code image under `state/` by default.
- Waits for QR confirmation.
- Writes the credential JSON to `--output`.
- Refuses to run unless `--acknowledge-risk` is present.
- Refuses to overwrite an existing credential file unless `--force` is passed.

Command shape:

```powershell
uv run python scripts/create_qqmusic_credential.py --output <path-to-qqmusic-credential.json> --qr-dir <qr-output-dir> --login-type <qq|wechat|mobile> --timeout-seconds <seconds>
```

`--login-type` accepts `qq`, `wechat`, or `mobile`. After reading the risk
notice and deciding to continue, add the required acknowledgement flag to the
command. Without that flag, the helper refuses to run.

Treat the generated JSON as a reusable QQ Music session credential. Anyone who
can read it may be able to act as your QQ Music session until the credential
expires or is revoked. Prefer a dedicated low-value QQ Music account for testing,
store the output outside Git and synced folders, and do not paste it into
issues, logs, or pull requests.

After creating the file, point `QQMUSIC_CREDENTIAL_PATH` at it:

```powershell
QQMUSIC_CREDENTIAL_PATH=<path-to-qqmusic-credential.json>
```
