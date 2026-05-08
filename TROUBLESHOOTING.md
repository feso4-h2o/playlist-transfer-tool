# Troubleshooting

## Invalid Spotify OAuth

Symptoms include missing credential preflight errors, browser callback failures,
or Spotify `401`/`403` responses.

- Confirm `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, and
  `SPOTIFY_REDIRECT_URI` are set in the environment used to run the CLI.
- Confirm the redirect URI exactly matches the value registered on the Spotify
  developer app.
- Delete the local Spotipy token cache if you changed app credentials or scopes.
  By default it is under `%LOCALAPPDATA%\playlist-porter\spotify-token-cache` on
  Windows and `~/.cache/playlist-porter/spotify-token-cache` elsewhere.

## Expired QQ Music Cookie

QQ Music session failures are treated as non-retryable.

- Refresh the QQ Music session in your normal local workflow.
- Update the JSON file referenced by `QQMUSIC_CREDENTIAL_PATH`.
- Rerun the same command; SQLite state lets the transfer continue from saved
  progress.

## Rate Limit Pause

Spotify `429` responses honor `Retry-After` before exponential fallback. QQ
Music uses conservative pacing and a circuit breaker for repeated failures.

- Wait before retrying if the service is throttling.
- Rerun the same transfer command after the pause.
- If QQ Music opens the circuit breaker repeatedly, refresh credentials and try
  a smaller playlist first.

## No Candidates Found

No-candidate results are exported with `no_candidates` or `low_confidence`
reason codes.

- Check spelling, artist order, and whether the track exists on the destination
  platform.
- Inspect `reports/unavailable-tracks.csv` for attempted queries and suggested
  alternates.
- Retry with a smaller or cleaner source playlist if source metadata is noisy.

## Wrong Version Selected

Live, remix, acoustic, remaster, instrumental, and similar version tags affect
the metadata score.

- Use `playlist-porter review` before writing.
- Reject incorrect candidates or accept the preferred candidate rank.
- Re-export reports after review to preserve unresolved tracks for manual
  follow-up.

## Python Dependency Issue

- Confirm the active checkout uses Python 3.12.
- Run `uv sync` to recreate the virtual environment from `uv.lock`.
- Run `uv run pytest` and `uv run ruff check .` after dependency changes.
- If a dependency breaks after an update, pin a known-good version in
  `pyproject.toml`, regenerate `uv.lock`, and document the reason in the PR.
