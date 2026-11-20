"""Argument parsing and user-facing command contracts."""

import argparse
import json
import sqlite3
import sys

from . import dashboard, demo, processes, releases, supervisor, tests_runner
from .config import REPO, load_policy, prepare_workspace, require_python, save_policy, saved_policy
from .locking import WorkspaceLock, held
from .manager import pointer, validate
from .models import INVALID, PREFLIGHT, DeployError
from .security import printable
from .storage import Store


def limit(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("provide an integer from 1 through 200") from exc
    if not 1 <= number <= 200:
        raise argparse.ArgumentTypeError("provide an integer from 1 through 200")
    return number


def parser():
    cli = argparse.ArgumentParser(prog="python -m deploysafe",
                                  description="Local deployment, health checks and automatic rollback.",
                                  epilog="Example: python -m deploysafe deploy --release 1.0.0")
    cli.add_argument("--config", help="project-contained policy JSON")
    cli.add_argument("--workspace", help="select a project-contained workspace")
    commands = cli.add_subparsers(dest="command", required=True)
    for name, description in (
        ("doctor", "validate Python, policy and the configured local ports"),
        ("init", "initialize a marked workspace and its audit database"),
        ("list-releases", "list the bundled release IDs"),
        ("validate-release", "run read-only release preflight"),
        ("deploy", "deploy a bundled sample"),
        ("status", "show the committed release and latest supervisor observation"),
        ("history", "show recent deployments, transitions, checks and incidents"),
        ("rollback", "activate an archived, previously verified release"),
        ("demo", "run bundled deterministic fault scenarios"),
        ("dashboard", "serve audit history on localhost in the foreground"),
        ("recover", "restart and verify the committed local release"),
        ("stop-demo", "stop the workspace supervisor and its owned releases"),
        ("test", "run bundled validation or the fixed unittest suite"),
    ):
        command = commands.add_parser(name, help=description, description=description)
        if name != "dashboard":
            command.add_argument("--json", action="store_true", help="write one JSON result to stdout")
        if name == "init":
            command.add_argument("--config", default=argparse.SUPPRESS, help="project-contained policy JSON")
        if name in ("validate-release", "deploy"):
            command.add_argument("--release", required=True, help="bundled release ID or path")
        if name == "rollback":
            command.add_argument("--to", required=True, help="previously verified release ID")
        if name == "history":
            command.add_argument("--limit", type=limit, default=20)
        if name == "dashboard":
            command.add_argument("--port", type=int)
        if name == "demo":
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument("--all", action="store_true")
            choice.add_argument("--scenario", help="a scenario ID such as 03_health_503")
        if name == "test":
            command.add_argument("--suite", action="store_true", help="run the checked-in unittest suite")
    return cli


def _policy(args):
    selected = load_policy(REPO, args.config or "config/default.json")
    if args.workspace is not None:
        selected = selected.with_workspace(args.workspace)
    existing = saved_policy(selected)
    if existing and args.config and existing != selected:
        raise DeployError("POLICY_CHANGED", "Use the workspace's saved policy or initialize a different workspace.",
                          INVALID)
    return existing or selected


def initialize(policy):
    prepare_workspace(policy, create=True)
    with WorkspaceLock(policy.workspace, "client", timeout=1):
        current = saved_policy(policy)
        if current and current != policy:
            raise DeployError("POLICY_CHANGED", "Workspace already uses a different policy.", INVALID)
        if not current:
            save_policy(policy)
        with Store(policy.workspace) as store:
            if not store.one("SELECT key FROM app_meta WHERE key='initialized'"):
                with store.db:
                    store.db.execute("INSERT INTO app_meta VALUES ('initialized','1')")
                    store._event(None, None, "INIT", "PASS", "POLICY",
                                 f"Ports {policy.active_port}, {policy.candidate_port}, {policy.dashboard_port}; "
                                 f"timeout {policy.health_timeout_seconds}s; {policy.health_attempts} attempts; "
                                 f"backoff {policy.backoff_seconds}s.")
    return {"workspace": str(policy.workspace), "policy": policy.as_dict()}


def status(policy):
    prepare_workspace(policy)
    with Store(policy.workspace, readonly=True) as store, store.snapshot():
        return supervisor.observed(policy, pointer(store.active()))


def doctor(policy):
    active = None
    if saved_policy(policy):
        snapshot = status(policy)
        if snapshot["live"]:
            active = snapshot["active"]
    checks = {}
    for purpose, port in (("active", policy.active_port), ("candidate", policy.candidate_port),
                          ("dashboard", policy.dashboard_port)):
        code = "OWNED_ACTIVE" if active and active["port"] == port else "AVAILABLE"
        if code == "AVAILABLE":
            try:
                processes.check_port(port)
            except DeployError as exc:
                code = exc.code
        checks[purpose] = {"port": port, "code": code}
    return {"python": sys.version.split()[0], "workspace": str(policy.workspace), "bind": "127.0.0.1",
            "ports": checks}


def _command(args, policy):
    name = args.command
    if name == "doctor":
        result = doctor(policy)
        return (PREFLIGHT if any(item["code"] == "PORT_IN_USE" for item in result["ports"].values()) else 0), result
    if name == "init":
        return 0, initialize(policy)
    if name == "list-releases":
        items = []
        for version in releases.BUNDLED_IDS:
            if releases.fixture_path(policy, version).is_dir():
                items.append(version)
        return 0, {"releases": items}
    if name == "validate-release":
        if saved_policy(policy):
            return 0, supervisor.request(policy, "validate", args.release)
        return 0, validate(policy, args.release)
    if name in ("deploy", "rollback", "recover", "stop-demo"):
        action = "stop" if name == "stop-demo" else name
        result = supervisor.request(policy, action, getattr(args, "release", getattr(args, "to", None)))
        return result.get("exit_code", 0), result
    if name == "status":
        return 0, status(policy)
    if name == "history":
        prepare_workspace(policy)
        with Store(policy.workspace, readonly=True) as store, store.snapshot():
            return 0, store.history(args.limit)
    if name == "demo":
        initialize(policy)
        result = demo.run_demo(policy, args.scenario)
        return (0 if result["passed"] == result["total"] else PREFLIGHT), result
    if name == "test":
        result = tests_runner.run_suite(policy) if args.suite else tests_runner.bundled_validation(policy)
        if saved_policy(policy):
            with Store(policy.workspace) as store:
                store.test_result("repository unittest" if args.suite else "bundled validation",
                                  result["passed"], result)
        return (0 if result["passed"] else PREFLIGHT), result
    prepare_workspace(policy)
    port = args.port if args.port is not None else policy.dashboard_port
    with dashboard.server(policy, port) as server:
        print(f"DeploySafe dashboard http://127.0.0.1:{port}/  (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0, None


def _print(name, result, as_json, code):
    if result is None:
        return
    if as_json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    elif name == "demo":
        for item in result["scenarios"]:
            label = "PASS" if item["passed"] else "FAIL"
            observed = item["observed"]
            print(f"{label} {item['scenario_id']} · {observed['outcome']} · "
                  f"{observed['failure_code']} · active {observed['result']['active_version']}")
            print(f"  {item['report']}")
        print(f"{result['passed']}/{result['total']} scenarios passed; {result['cleanup']}.")
    elif name == "history":
        for row in result["deployments"]:
            print(f"{row['started_at']}  {row['action']:<8} {row['to_release'] or '-':<22} "
                  f"{row['state']:<16} {row['error_code'] or '-'}  {row['run_id']}")
        if not result["deployments"]:
            print("No deployments yet.")
        for row in result["events"]:
            print(f"{row['timestamp']}  {row['stage']:<16} {row['code']:<22} {row['message']}")
    elif "message" in result:
        output = sys.stderr if code else sys.stdout
        print(f"{result['code']}: {result['message']}", file=output)
        if result.get("run_id"):
            print(f"Run {result['run_id']} · {result['state']} · active {result['active_version']}", file=output)
    elif name == "list-releases":
        print("\n".join(result["releases"]))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=True))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        require_python()
        code, result = _command(args, _policy(args))
        _print(args.command, result, getattr(args, "json", False), code)
        return code
    except DeployError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"code": exc.code, "message": str(exc), "exit_code": exc.exit_code}))
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return exc.exit_code
    except (OSError, sqlite3.Error, ValueError) as exc:
        message = printable(exc)
        if getattr(args, "json", False):
            print(json.dumps({"code": "LOCAL_STATE", "message": message, "exit_code": PREFLIGHT}))
        print(f"LOCAL_STATE: {message}", file=sys.stderr)
        return PREFLIGHT
    except KeyboardInterrupt:
        print("Interrupted. Inspect status before retrying an in-flight deployment.", file=sys.stderr)
        return 130
