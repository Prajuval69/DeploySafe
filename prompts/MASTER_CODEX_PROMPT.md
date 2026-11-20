# MASTER CODEX BUILD PROMPT — DEPLOYSAFE

You are a senior DevOps engineer, Python systems engineer, reliability-focused tester, Windows/Linux script reviewer, and technical documentation author.

## The outcome
Build **DeploySafe**, a fully functional, offline, stdlib-only, locally scoped deployment lab that safely launches versioned *bundled demo Python HTTP services* on 127.0.0.1, validates release manifests/configuration, runs preflight + smoke + post-deployment checks, detects bad releases, reverts to a previously healthy version automatically, records append-only audit history in SQLite, and displays real status and events in a tiny local web dashboard.

**This is NOT an AWS deployment system, real infrastructure administration platform, or a container solution.** Use real local process and HTTP mechanics, not a hard-coded cinematic simulation. Use deterministic fixture replay alongside actual subprocess/HTTP integration tests.

## Absolute constraints
- Python 3.11+ **stdlib only** for application, tests, fixtures and UI. `pip install` must NOT be necessary.
- No AWS, Docker, Kubernetes, VM, external APIs, external database, paid services, browser CDN, Node/npm, cloud credentials or internet access for functionality.
- Run as an ordinary user on Windows 10/11 and Linux. Do not require admin privileges.
- Only manage child processes explicitly launched and recorded by DeploySafe. Never kill a process solely by PID number if process identity cannot be verified. Never run `taskkill`/`killall` on arbitrary names or use sudo.
- Only write within `.deploysafe_workspace` rooted under the repository by default, including temporary staging and release copies. Never delete or overwrite user files outside this directory; refuse unsafe workspace paths/symlinks.
- Bind demo servers and dashboard only to 127.0.0.1; enforce a conservative allowlist of ports. No arbitrary endpoints, SSRF, shell execution or unchecked URLs.
- No realistic secrets stored in fixtures. Sanitize logs and reject invalid configuration.

## Required architecture
1. `deploysafe/config.py`: typed/validated JSON policy; safe defaults, timeout and retries bounds, sandbox root, port checks, hostile configuration rejection.
2. `deploysafe/releases.py`: semver-ish release IDs; manifest schema; SHA256 on `app.py` and `app_config.json`; trust only allowed fixture roots; safe deep copy to workspace; reject traversal/symlinks.
3. `deploysafe/processes.py`: subprocess launch using `sys.executable`, `shell=False`, tracked `Popen` handles, stdout/stderr log redirection; ownership checks; cleanup of only owned processes; unique ports.
4. `deploysafe/health.py`: real localhost HTTP GET `/health`, `/version`, response schema & matching `version` checks, timeouts and bounded retries; distinguish timeout, connection-refused, non-2xx, wrong release and malformed JSON.
5. `deploysafe/storage.py`: SQLite schema/migrations, WAL, foreign keys, releases, deployments, events, checks, pointer changes, tests, incidents; transactions and indices; immutable audit history.
6. `deploysafe/manager.py`: state machine and deployment transaction: `STAGED -> PREFLIGHT_OK -> STARTING -> HEALTHY -> ACTIVE`; on fail `FAILED -> ROLLING_BACK -> ROLLED_BACK` or `RECOVERY_FAILED`. Previous healthy version remains available and must be verified after recovery. Use atomic manifest/pointer file replace; no symlink requirement.
7. `deploysafe/locking.py`: cross-platform exclusive bounded lock via create-excl and run-specific token; protect against concurrent deploy; stale lock handling only with demonstrable safety; never delete someone else's live lock.
8. `deploysafe/tests_runner.py`: on-demand `unittest` and built-in validation; no test runner that executes arbitrary user strings.
9. `deploysafe/cli.py` and `__main__.py`: `doctor`, `init`, `list-releases`, `validate-release`, `deploy`, `status`, `history`, `rollback`, `demo`, `dashboard`, `stop-demo`; clear errors & exit codes.
10. `deploysafe/dashboard.py`: read-only browser UI on localhost using `http.server`; build HTML/CSS/SVG offline, no JS dependency/CDN. Show active version, health, recent events, rollout results, failure reasons and recovery status. Dashboard write actions not required; can be omitted for safety.
11. `deploysafe/demo.py`: run all 8 scenario manifests deterministically; cover real HTTP process tests for baseline, successful update, healthfail, timeout, invalid config, checksum tampering, startup crash, wrong version response. Each scenario generates a machine-readable report.
12. `scripts/` include clearly labeled PowerShell and Bash wrappers for `doctor`, `deploy`, `status`, `demo` with robust quoting; no admin or service changes. Tests/mock adapters for OS-specific behavior; never claim cross-platform manual success if not run.
13. `tests/`: **45+ meaningful** `unittest` tests including real bounded local integration tests. No filler tests or unconditional `assertTrue(True)`.
14. `docs/`: user guide, architecture, state-machine diagram, verification evidence, Windows and Linux instructions, runbooks, security and known limits, honest resume bullets.

## Suggested output tree
```
deploysafe/{__init__,__main__,cli,config,releases,processes,health,
           storage,locking,manager,demo,dashboard,models,security}.py
tests/{test_config,test_releases,test_processes,test_health,test_storage,
       test_manager,test_rollback,test_security,test_cli,test_demo,test_integration}.py
scripts/{windows/deploy.ps1,windows/demo.ps1,linux/deploy.sh,linux/demo.sh}
config/{default.json,strict.json,scenario_policy.json}
fixtures/demo_releases/*/{app.py,app_config.json,release.json}
fixtures/scenarios/*.json
.deploysafe_workspace/  (generated by application only)
docs/IMPLEMENTATION_PROGRESS.md  docs/OPERATIONS_RUNBOOK.md docs/EVIDENCE.md
README.md  .gitignore  AGENTS.md
```

## Failure lab semantics
- V1 baseline healthy deploy succeeds.
- V2 healthy version upgrade succeeds and active pointer changes.
- V3 returns 503 on `/health`; reject, retain/restore V2 and verify it.
- V4 invalid config is blocked before launch.
- V5 stalls HTTP beyond a short health timeout; reject, recover V2.
- V6 manifest checksum mismatch is blocked before staging/launch.
- V7 raises an exception during startup; reject, recover V2.
- V8 responds with `healthy` but wrong release version; reject, recover V2.
Follow scenario expectations in `fixtures/scenarios/` and `docs/12_FAILURE_LAB.md`. Keep process/port cleanup deterministic.

## Security and lifecycle rules
Treat everything in `release.json` as untrusted, validate all fields, compare SHA256, reject symlinks and out-of-root traversal, avoid shell strings. Handle partial deploy + power interruption with persisted state recovery. On failure produce human-readable explanation, not an opaque success. Implement short per-stage timeouts. Use context managers and cleanup, avoid port leaks. Keep provenance fields of events and releases.

## Required quality gates
- `python tools/verify_kit.py` passes.
- `python -m deploysafe --help` works.
- `doctor`, `init`, valid deploy, bad deploy + rollback, history, status and dashboard work locally.
- Deterministic full failure lab demonstrates all bundled cases.
- SQLite history and immutable audit events survive restart.
- 45+ substantive tests pass and include security + real process integration.
- Windows and Linux scripts have proper quoting and no privileged operations.
- Documentation records what was actually run, on which OS, and any unverified capabilities.

## Execution requirement
Don't stop after writing plans. Implement modules, run tests, debug failures, execute deterministic demos, collect real evidence, and give a final summary of verified commands. If OS-specific runs can't be performed, state so clearly. If true external dependencies are missing, you may not substitute cloud services or install Docker; the implementation should succeed with stdlib only.
