# Deterministic local release fixtures

`demo_releases/` contains real tiny stdlib Python HTTP servers. Their behaviors come entirely from `app_config.json`; each release has `release.json` with SHA256 digests. They are **demonstration targets**, not DeploySafe itself. The files are generated deterministically by `tools/generate_demo_fixtures.py`.

- `1.0.0`: initial healthy baseline.
- `1.1.0`: healthy upgrade.
- `1.2.0-healthfail`: `/health` returns 503.
- `1.3.0-badconfig`: invalid `bind` (must be caught before start).
- `1.4.0-timeout`: `/health` waits beyond bounded timeout.
- `1.5.0-tampered`: app.py intentionally modified after manifest generation; SHA256 mismatch.
- `1.6.0-crash`: app.py exits during startup with controlled exception.
- `1.7.0-wrongversion`: health reports a version other than the release ID.

**Never deploy untrusted user-supplied Python scripts.** These release fixtures are intentionally simple, local-only educational examples. Tests should copy fixtures into temporary directories and clean up child processes safely. Use the JSON scenario expectations in `scenarios/`.
