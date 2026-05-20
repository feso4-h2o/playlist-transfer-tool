# Repository Guidelines

## Maintenance Snapshot

Last checked against commit `4375553` on 2026-05-20.

## Project Structure & Module Organization

This is a Python 3.12 CLI package for one-way Spotify/QQ Music playlist transfer. Source lives in `src/playlist_porter/`; `cli.py` exposes `playlist-porter`, and `workflow.py` coordinates matching, review, writes, and reports.

Key packages: `platforms/` adapters, `matching/`, `persistence/`, `rate_limit/`, and `review/`. Tests live in `tests/unit/`; sample metadata lives in `fixtures/`.

## Build, Test, and Development Commands

Use `uv` locally.

- `uv sync`: install.
- `uv run playlist-porter init-config --path cli-config.json`: create local config.
- Edit config-owned workflow values in `cli-config.json`, then run `uv run playlist-porter match --config cli-config.json`.
- Continue the same run with `uv run playlist-porter review --config cli-config.json`, `uv run playlist-porter write --config cli-config.json`, and `uv run playlist-porter export-report --config cli-config.json`.
- `uv run ruff check .` and `uv run pytest`: required PR checks.

## Agent Operation Notes

Local Git commands need the repository-scoped safe-directory flag. Use `git -c safe.directory=D:/GitHub/Projects/playlist-transfer-tool ...` for status, branch, log, diff, push, and worktree commands.

For PR publishing, use local `git`/`gh`; prefer `gh pr create --base main --head <branch> --title "<title>" --body "<body>"`.

## Coding Style & Naming Conventions

Follow Ruff settings in `pyproject.toml`: Python 3.12, 100-character lines, rules `E`, `F`, `I`, `UP`, and `B`. Use 4-space indentation, public type annotations, `snake_case`, and `PascalCase` for classes.

## Testing Guidelines

Tests use pytest with strict markers and strict config. Add focused unit tests under `tests/unit/test_<area>.py`, named `test_<expected_behavior>()`. Cover normalization, matching, persistence, CLI workflow, writes/resume behavior, fixtures, and adapters.

Unit tests must not require live network access. Live fixture refreshes require opt-in, such as `RUN_LIVE_SPOTIFY_TESTS=1` or `RUN_LIVE_QQMUSIC_TESTS=1`, and must scrub credentials, cookies, private IDs, and sensitive headers.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commits-style subjects, for example `fix: retry late empty qqmusic searches`. Use `<type>: <lowercase summary>` with `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `build`, or `ci`.

PRs should summarize changes, validation, and related issues.

## Security & Configuration Tips

Do not commit credentials, tokens, QQ Music cookies, databases, reports, caches, or local agent notes. Spotify values belong in `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, and `SPOTIFY_SCOPES`; QQ Music credentials belong in local JSON referenced by `QQMUSIC_CREDENTIAL_PATH`. Local config, state, and report files should stay ignored.
