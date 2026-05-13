# Configuration

Playlist Porter separates local run configuration from credentials.

`playlist-porter.json` owns local paths, mock fixture paths, platform behavior
flags, and optional `commands.*` defaults. Spotify and QQ Music credentials are
environment-only inputs so secrets do not end up in project config.

## Starter Config

Create a local config:

```powershell
uv run playlist-porter init-config --path playlist-porter.json
```

The generated file includes:

- `database_path`: SQLite transfer state path.
- `report_output_dir`: default report output directory.
- `mock`: fixture and mock-write paths.
- `spotify`: local Spotify behavior such as token cache path and playlist
  visibility for created playlists.
- `qqmusic`: QQ Music adapter behavior flags and page size.
- `commands`: optional defaults for `match`, `review`, `write`, and
  `export-report`.

Explicit CLI flags override values from `commands.*`.

## Environment Loading

Copy `.env.example` to `.env` or another local environment manager. The CLI does
not auto-load `.env`; use `uv --env-file` for local runs:

```powershell
uv run --env-file .env playlist-porter match --config playlist-porter.json
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
spaces. If omitted, the CLI uses the default playlist read/write scopes.

The first Spotify command that needs OAuth may ask you to open a browser URL and
paste the redirected callback URL. Paste the full URL, including the `?code=...`
query string.

## QQ Music Setup

QQ Music credentials are also environment-only:

```powershell
QQMUSIC_CREDENTIAL_PATH=<path-to-local-credential-json>
QQMUSIC_USER_ID=<optional-user-id>
```

`QQMUSIC_CREDENTIAL_PATH` should point to a local JSON credential accepted by
`qqmusic_api.Credential`. `QQMUSIC_USER_ID` is optional and only needed for
local workflows that require an explicit account identifier.

### Optional Credential Helper

QQ Music writes require a local session credential. The main CLI does not create
or refresh that credential automatically. If you choose to use
`qqmusic-api-python`'s unofficial QR login flow, this repository includes a
helper:

```powershell
uv run python scripts/create_qqmusic_credential.py --output state/qqmusic-credential.json --acknowledge-risk
```

The helper:

- Prints a risk notice before login.
- Saves a QR code image under `state/` by default.
- Waits for QR confirmation.
- Writes the credential JSON to `--output`.
- Refuses to run unless `--acknowledge-risk` is present.
- Refuses to overwrite an existing credential file unless `--force` is passed.

Useful options:

```powershell
uv run python scripts/create_qqmusic_credential.py --output state/qqmusic-credential.json --qr-dir state --login-type qq --timeout-seconds 180 --acknowledge-risk
```

`--login-type` accepts `qq`, `wechat`, or `mobile`.

Treat the generated JSON as a reusable QQ Music session credential. Anyone who
can read it may be able to act as your QQ Music session until the credential
expires or is revoked. Prefer a dedicated low-value QQ Music account for testing,
store the output outside Git and synced folders, and do not paste it into
issues, logs, or pull requests.

After creating the file, point `QQMUSIC_CREDENTIAL_PATH` at it:

```powershell
QQMUSIC_CREDENTIAL_PATH=state/qqmusic-credential.json
```
