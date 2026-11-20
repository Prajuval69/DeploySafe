"""Replay the supplied scenario expectations against real local processes."""

from dataclasses import replace
import re
import uuid

from .config import load_policy, prepare_workspace, save_policy
from .locking import WorkspaceLock
from .manager import Manager
from .models import DeployError, INVALID, utc_now
from .security import atomic_json, inside, read_json, release_id
from .storage import STATES, Store

ID = re.compile(r"0[1-8]_[a-z0-9_]+")
FIELDS = {"description", "expected_active_version", "expected_failure_code", "expected_outcome",
          "initial_active_version", "release_id", "requires_admin", "requires_cloud", "scenario_id"}
STAGES = {"NONE": "ACTIVE", "PREFLIGHT_INVALID_BIND": "PENDING", "CHECKSUM_MISMATCH": "PENDING",
          "HTTP_503": "STARTING", "HEALTH_TIMEOUT": "STARTING", "PROCESS_EXITED": "STARTING",
          "WRONG_VERSION": "STARTING"}


def scenarios(repo, selection=None):
    root = inside(repo, "fixtures/scenarios")
    if selection is not None and not ID.fullmatch(selection):
        raise DeployError("INVALID_SCENARIO", "Use an ID from fixtures/scenarios.", INVALID)
    paths = sorted(root.glob("*.json")) if selection is None else [root / f"{selection}.json"]
    if selection is None and len(paths) != 8:
        raise DeployError("INVALID_SCENARIO", "Expected eight bundled scenario manifests.", INVALID)
    for path in paths:
        try:
            data = read_json(repo, path)
        except FileNotFoundError as exc:
            raise DeployError("INVALID_SCENARIO", "Scenario was not found.", INVALID) from exc
        if (type(data) is not dict or set(data) != FIELDS or not ID.fullmatch(path.stem)
                or data["scenario_id"] != path.stem or data["requires_admin"] is not False
                or data["requires_cloud"] is not False or type(data["description"]) is not str
                or type(data["expected_failure_code"]) is not str
                or data["expected_failure_code"] not in STAGES
                or data["expected_outcome"] not in ("SUCCESS", "REJECTED_AND_RETAINED")):
            raise DeployError("INVALID_SCENARIO", "Invalid bundled scenario manifest.", INVALID)
        release_id(data["release_id"])
        release_id(data["expected_active_version"])
        if data["initial_active_version"] is not None:
            release_id(data["initial_active_version"])
        yield data


def _bootstrap(manager, version):
    if version is None:
        return
    for baseline in ("1.0.0", "1.1.0") if version == "1.1.0" else (version,):
        result = manager.deploy(baseline)
        if result.exit_code:
            raise DeployError("DEMO_SETUP", f"Could not establish baseline {baseline}: {result.message}",
                              result.exit_code)


def _run_one(policy, manager, store, case, episode):
    starting = manager.status()
    previous = starting["active"]["release_id"] if starting["active"] else None
    result = manager.deploy(case["release_id"])
    status = manager.status()
    events = store.rows("SELECT * FROM deployment_events WHERE run_id=? ORDER BY id", (result.run_id,))
    checks = store.rows("SELECT * FROM health_checks WHERE run_id=? ORDER BY id", (result.run_id,))
    preflight = store.rows("SELECT * FROM preflight_checks WHERE run_id=? ORDER BY id", (result.run_id,))
    stages = [event["stage"] for event in events if event["stage"] in STATES]
    failure_stage = stages[stages.index("FAILED") - 1] if "FAILED" in stages else "ACTIVE"
    expected_code = case["expected_failure_code"]
    observed_code = "NONE" if result.exit_code == 0 else result.code
    outcome = {"ACTIVE": "SUCCESS", "ROLLED_BACK": "REJECTED_AND_RETAINED"}.get(result.state, result.state)
    passed = (previous == case["initial_active_version"] and outcome == case["expected_outcome"]
              and observed_code == expected_code and failure_stage == STAGES[expected_code]
              and result.active_version == case["expected_active_version"] and status["live"]
              and (previous is None or starting["live"]))
    report = {"scenario_id": case["scenario_id"], "passed": passed, "timestamp": utc_now(),
              "workspace": episode, "source": "bundled scenario + actual loopback deployment",
              "expected": case, "observed": {"outcome": outcome, "failure_code": observed_code,
              "failure_stage": failure_stage, "result": result.as_dict(), "service": status},
              "events": events, "health_checks": checks, "preflight_checks": preflight}
    path = f"reports/{episode}-{case['scenario_id']}.json"
    atomic_json(policy.workspace, path, report)
    store.test_result(case["scenario_id"], passed, {"report": path, "failure_code": observed_code})
    return {**report, "report": path}


def run_demo(policy, selection=None):
    cases = list(scenarios(policy.repo, selection))
    episode = uuid.uuid4().hex
    lab = replace(load_policy(policy.repo, "config/scenario_policy.json"), active_port=8865,
                  candidate_port=8866, dashboard_port=8887).with_workspace(policy.workspace / "demos" / episode)
    reports = []
    with WorkspaceLock(policy.workspace, "demo"):
        prepare_workspace(lab, create=True)
        save_policy(lab)
        with Store(lab.workspace) as store:
            manager = Manager(lab, store)
            try:
                manager.reconcile()
                _bootstrap(manager, cases[0]["initial_active_version"])
                for case in cases:
                    reports.append(_run_one(policy, manager, store, case, episode))
            finally:
                manager.stop()
        run = {"workspace": str(lab.workspace), "scenarios": reports,
               "passed": sum(report["passed"] for report in reports), "total": len(cases),
               "cleanup": "owned processes stopped"}
        atomic_json(policy.workspace, f"reports/{episode}-summary.json", run)
        return run
