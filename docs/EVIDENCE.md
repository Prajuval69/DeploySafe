# Verification evidence

Verified on 2026-11-20 using Windows NT 10.0, build 26300 (x64), CPython 3.12.4, and PowerShell 7.6.9. The workload used project-contained files and loopback HTTP. Windows Git Bash reported GNU Bash 5.2.37, `x86_64-pc-msys`.

## Commands and observed results

| Command or workflow | Observed result |
| --- | --- |
| `python tools/verify_kit.py` | Validated 23 numbered documents, eight releases, and eight scenarios before implementation. Repeated successfully after the build. |
| `python -B -m unittest discover -s tests -v` | **78 passed**, zero failures, errors, or skips in **16.954 seconds**. |
| `python -B -m deploysafe test --suite --json` | The on-demand test runner completed the same suite and returned `"passed": true` with test-process exit 0. |
| `python -B -m deploysafe --help`, `doctor`, `init`, `list-releases`, `validate-release`, and `test` | Help, policy initialization, bundled inventory, dry-run validation, port checks and built-in validation completed. |
| Deploy 1.0.0, upgrade to 1.1.0, attempt 1.2.0-healthfail | Both healthy releases activated. The fault returned exit 4 and recorded `HTTP_503` and `ROLLED_BACK`. A separate status invocation confirmed live 1.1.0. |
| `history --limit 20 --json` and `rollback --to 1.0.0` | History retained the rejected release and its recovery steps. Explicit rollback verified and activated the saved 1.0.0 artifact. |
| `dashboard --port 8787` | Actual GET returned HTTP 200 with committed 1.1.0, `HTTP_503`, `ROLLED_BACK` and `PRIOR_HEALTHY` in the rendered HTML. A POST returned HTTP 405. |
| `demo --all` and `scripts/windows/demo.ps1` | All eight scenarios matched their expected codes and active versions, with per-scenario JSON evidence and final cleanup. |
| Windows PowerShell wrappers | All four executed. The deploy wrapper propagated exit 4 for the HTTP 503 fixture. |
| Git Bash wrappers | All four passed `bash -n` and ran against Windows Python through a temporary `python3` shim. The deploy wrapper propagated exit 4 and the selected crash demo passed. |
| `stop-demo` and final `doctor` | The supervisor shut down. The default service and dashboard ports were available again. |

The suite exercises actual child processes, localhost timeout and fault responses, an independently held socket, cross-invocation status, and recovery after terminating a supervisor owned by the test. The interrupted-commit state is tested with injected divergence between the pointer and SQLite. The suite exercises a real hard link and uses mocked filesystem metadata for symbolic links and Windows reparse points.

## Failure lab

| Scenario | Observed result | Active version after the operation |
| --- | --- | --- |
| `01_initial_good` | Activated | 1.0.0 |
| `02_upgrade_good` | Activated | 1.1.0 |
| `03_health_503` | `HTTP_503` | 1.1.0 |
| `04_invalid_config` | `PREFLIGHT_INVALID_BIND` | 1.1.0 |
| `05_health_timeout` | `HEALTH_TIMEOUT` | 1.1.0 |
| `06_checksum_tamper` | `CHECKSUM_MISMATCH` | 1.1.0 |
| `07_startup_crash` | `PROCESS_EXITED` | 1.1.0 |
| `08_wrong_version` | `WRONG_VERSION` | 1.1.0 |

The final full lab run reported **8/8 scenarios passed**. The six rejections each verified the retained healthy release.

## Local evidence files

- Full unittest output (`.deploysafe_workspace/reports/windows-unittest.txt`)
- Sanitized Windows command transcript (`.deploysafe_workspace/reports/windows-smoke.json`)
- HTML captured from the live dashboard (`.deploysafe_workspace/reports/windows-dashboard.html`)
- Final eight-scenario JSON report (`.deploysafe_workspace/reports/c144d806ffaa4bf6b15adbf9aeaa7ad7-summary.json`)

These files were generated inside the local workspace. Running the commands again generates new timestamps and scenario run IDs. See the [operations runbook](OPERATIONS_RUNBOOK.md) for reproduction steps.

## Verification limits

Native Linux runtime behavior, the POSIX locking branch, and Python 3.11 were unverified on this host. The recovery test terminated a test-owned supervisor and restarted its committed fixture. Actual machine power loss was outside this test run. Dashboard evidence came from the served HTTP response.

## Portfolio wording supported by the run

- Built a Python standard-library deployment lab that staged and verified bundled services, checked real HTTP health, and automatically retained a healthy release after candidate failure.
- Recorded deployment transitions, health attempts, and recovery outcomes in SQLite and verified 78 tests and eight fault lab scenarios on Windows.
