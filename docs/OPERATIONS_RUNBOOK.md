# DeploySafe operations runbook

Run commands from the repository root with Python 3.11 or newer. The examples use `python` on Windows. On Linux use `python3` if needed. Start with `doctor` for port checks, `status --json` for the active release and health observation, and `history --limit 30 --json` for the recent audit.

| Situation | Inspect | Recover and verify |
| --- | --- | --- |
| Clean start | Run `python -B tools/verify_kit.py` and `python -B -m deploysafe doctor`. | Run `python -B -m deploysafe init --config config/default.json`, deploy `1.0.0`, and confirm `live: true` in status. |
| Routine update | Run `python -B -m deploysafe validate-release --release 1.1.0` and check the candidate port. | Deploy `1.1.0`. Confirm the new port and release in status. Check the `HEALTHY` and `ACTIVE` events. |
| HTTP 503 | Deploy `1.2.0-healthfail` after `1.1.0`. History records `HTTP_503`. | Confirm exit 4, a `ROLLED_BACK` event, and live 1.1.0. In JSON history look for HTTP checks under the `rollback` phase. |
| Health timeout | Deploy `1.4.0-timeout`. Look for `HEALTH_TIMEOUT` and bounded retries in the check history. | Verify the previous release is still live. To reproduce independently, run `demo --scenario 05_health_timeout`. |
| Corrupt manifest or checksum | Run `validate-release --release 1.5.0-tampered` for the bundled mismatch. Inspect `INVALID_MANIFEST`, `INVALID_JSON`, or `CHECKSUM_MISMATCH` for an unexpected local change. | Restore the fixture from the original kit if it was changed accidentally. Validate a healthy fixture and inspect the prior active release. The tampered fixture should continue to fail validation. |
| Startup crash | Deploy `1.6.0-crash`. Find `PROCESS_EXITED` and the `logs/<instance>.log` entry in history. | Confirm the controlled startup exception in the log and prior health in status. Try a verified healthy sample if a fresh candidate is needed. |
| Wrong version | Deploy `1.7.0-wrongversion`. History records `WRONG_VERSION`. | Check that the previous version remained active and that the candidate port is available afterward. |
| Port conflict | Run `doctor` and inspect the `PORT_IN_USE` preflight entry. Check the active marker and known DeploySafe workspaces. | If the conflict is a known DeploySafe run, stop that workspace with its own `stop-demo`. Otherwise use a fresh explicitly configured workspace with three free allowed ports, or wait for the other owner to release the port. Re-run doctor. |
| Pointer divergence or supervisor loss | Compare `runtime/ACTIVE.json` with the committed release shown by `status --json`. Check history and `logs/*.supervisor.log`. | Run `recover`, then check live status and `POINTER_RECONCILED` or `RESTORED`. Preserve the database for diagnosis if recovery reports failure. |
| Lock conflict | Exit 6 indicates a concurrent request or operation. Check status and history. | Let the existing request finish and retry. Kernel locks determine ownership. Keep the guard files in place. |
| Recovery failed | Exit 5 indicates the prior release could not be verified or cleanup failed. Review the recovery incident, owned-process log, and doctor output. | Address the recorded workspace or port issue and run `recover`. If the archived release is unavailable, use a retained verified version with `rollback --to VERSION`, or start a fresh workspace. Confirm live health after any change. |
| Shutdown | Run `stop-demo` for the selected workspace. Use Ctrl+C in any terminal running the dashboard or the foreground fault lab. | Check `status --json` for a cleared active release and rerun `doctor`. Inspect history to confirm it was preserved. |

Expand abbreviated commands with `python -B -m deploysafe`. The failure lab stores its reports under `reports/` and its isolated SQLite history and logs under `demos/<run-id>/`. Read an isolated workspace after cleanup with `--workspace .deploysafe_workspace/demos/<run-id> history --json`.

### Loopback service checks

```powershell
$Status = python -B -m deploysafe status --json | ConvertFrom-Json
if ($Status.live) {
    Invoke-RestMethod "http://127.0.0.1:$($Status.active.port)/health"
}
```

```bash
python3 -B -m deploysafe status --json
# Use the port shown by status. 8765 is the initial default slot.
python3 -B -c 'import urllib.request; print(urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:8765/health", timeout=2).read().decode())'
```

### Preserve or clear a workspace

Stop its supervisor and dashboard first. Review the resolved directory and confirm its `.deploysafe-owner.json` names that exact path. Preserve `db/`, `logs/`, `reports/`, and `demos/` if you want the local evidence. To clear a stopped run, remove only that marked workspace using the file manager. `init` can recreate a new workspace at the default location. Keep the supplied fixture directory intact.
