# DeploySafe — Local DevOps Deployment, Testing & Automatic Rollback

DeploySafe runs versioned, bundled Python HTTP services on loopback. It checks each release before launch, verifies the candidate over HTTP, and keeps the previous healthy service running until activation succeeds. SQLite preserves the history of deployments, health checks, rollback decisions, and incidents.

Use Python 3.11 or newer from the repository root. The application and tests use the Python standard library and run offline with an ordinary user account.

## Try a deployment

```text
python -B tools/verify_kit.py
python -B -m deploysafe doctor
python -B -m deploysafe init --config config/default.json
python -B -m deploysafe list-releases
python -B -m deploysafe validate-release --release fixtures/demo_releases/1.0.0
python -B -m deploysafe deploy --release 1.0.0
python -B -m deploysafe deploy --release 1.1.0
python -B -m deploysafe deploy --release 1.2.0-healthfail
python -B -m deploysafe status --json
python -B -m deploysafe history --limit 20
```

The third deploy returns exit 4. Its SQLite event trail records the candidate's HTTP 503 and a successful recovery check against 1.1.0. Status shows the current service address in `active.port` because the two service slots alternate on successful deployments.

In a second terminal, run `python -B -m deploysafe dashboard --port 8787` and open [the local dashboard](http://127.0.0.1:8787/). Reload to refresh the snapshot. Ctrl+C stops the dashboard.

```text
python -B -m deploysafe rollback --to 1.0.0
python -B -m deploysafe stop-demo
```

Explicit rollback starts the selected verified archive as a fresh candidate. `stop-demo` stops the managed service and clears the active pointer while preserving history.

On Linux use `python3` if that is the local Python 3.11+ command. The examples use `-B` to suppress CPython bytecode files beside the source.

## Run the failure lab and tests

```text
python -B -m deploysafe demo --all
python -B -m deploysafe demo --scenario 05_health_timeout
python -B -m deploysafe test
python -B -m unittest discover -s tests -v
```

The lab uses loopback ports 8865 and 8866. It starts in a separate workspace for each invocation, establishes the required baseline for individual scenarios, and closes its owned processes after collecting results. Reports under `.deploysafe_workspace/reports/` contain expected and observed outcomes, lifecycle events, and HTTP checks. Each run preserves its own SQLite database and logs under `.deploysafe_workspace/demos/`. To inspect a run after cleanup, pass its workspace path to `--workspace` before `history` or `dashboard`. See [actual verification results](docs/EVIDENCE.md).

## Optional launchers

| Operation | PowerShell | Bash |
| --- | --- | --- |
| Doctor | `./scripts/windows/doctor.ps1` | `bash scripts/linux/doctor.sh` |
| Deploy | `./scripts/windows/deploy.ps1 -Release '1.0.0'` | `bash scripts/linux/deploy.sh '1.0.0'` |
| Status | `./scripts/windows/status.ps1 -Json` | `bash scripts/linux/status.sh --json` |
| Demo | `./scripts/windows/demo.ps1 -Scenario '03_health_503'` | `bash scripts/linux/demo.sh '03_health_503'` |

If shell script execution is restricted, use the Python commands directly.

## CLI reference

| Command | Effect |
| --- | --- |
| `doctor` | Checks Python, the effective policy, and the configured local ports. |
| `init --config config/default.json` | Creates the marked workspace, saves its policy, and initializes SQLite. |
| `list-releases` | Lists the eight bundled samples, including intentional fault fixtures. |
| `validate-release --release ID_OR_PATH` | Checks artifact integrity, configuration, workspace access, and candidate port availability. |
| `deploy --release ID_OR_PATH` | Starts and verifies a bundled release, then activates it or attempts recovery. |
| `status --json` | Shows the committed release and the most recent supervisor health observation. |
| `history --limit 20 --json` | Shows deployments, events, HTTP checks, incidents, and pointer changes. |
| `rollback --to VERSION` | Activates a retained, previously verified release. |
| `recover` | Reconciles the pointer with SQLite and verifies the committed release, restarting it if needed. |
| `stop-demo` | Stops the workspace supervisor and its owned release processes. |
| `demo --all` / `demo --scenario ID` | Runs all eight scenarios or one selected scenario. |
| `test` / `test --suite` | Verifies the bundled fixtures or runs the checked-in unittest suite. |
| `dashboard --port 8787` | Serves a read-only, foreground snapshot on loopback. |

Exit codes are **0** success, **2** invalid input or unavailable rollback target, **3** preflight rejection, **4** candidate or activation failure, **5** recovery failure, **6** lock contention, and **130** interactive interruption. A rejected candidate can leave a healthy previous version serving even though the command returns a failure code.

The default workspace is `.deploysafe_workspace/`. An alternate workspace must be explicitly selected with `--workspace` or a project-contained policy. It must have an existing parent and be empty when first claimed. A workspace keeps its saved policy across invocations. Use a fresh workspace to change its ports or retry limits.

See [architecture](docs/ARCHITECTURE.md), [operations runbook](docs/OPERATIONS_RUNBOOK.md), and [security limits](docs/SECURITY_LIMITS.md). This project targets local release-engineering practice with the included fixtures.
