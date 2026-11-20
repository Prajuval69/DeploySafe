import contextlib
import io
import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from deploysafe import cli, dashboard, demo, health, processes, supervisor
from deploysafe.config import REPO
from deploysafe.manager import Manager, POINTER
from deploysafe.security import atomic_json, read_json
from deploysafe.storage import Store

from support import workspace


class CliTests(unittest.TestCase):
    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_stdout_remains_machine_readable_on_rejection(self):
        code, output, error = self.invoke("validate-release", "--release", "1.5.0-tampered", "--json")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output)["code"], "CHECKSUM_MISMATCH")
        self.assertIn("CHECKSUM_MISMATCH", error)

    def test_help_exports_all_primary_commands(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                cli.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        for name in ("deploy", "rollback", "dashboard", "demo", "stop-demo", "recover"):
            self.assertIn(name, output.getvalue())

    def test_history_limit_is_bounded_at_parser(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as raised:
                cli.main(["history", "--limit", "201"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("1 through 200", stderr.getvalue())

    def test_dry_run_does_not_create_deployment_or_pointer(self):
        with workspace() as policy, Store(policy.workspace) as store:
            code, output, _ = self.invoke("--workspace", str(policy.workspace), "validate-release",
                                          "--release", "1.0.0", "--json")
            self.assertEqual(code, 0)
            self.assertIn("checksums", json.loads(output)["checks"])
            self.assertEqual(store.history(10)["deployments"], [])
            self.assertFalse((policy.workspace / POINTER).exists())

    def test_builtin_validation_checks_all_expected_release_results(self):
        code, output, _ = self.invoke("test", "--json")
        result = json.loads(output)
        self.assertEqual((code, result["passed"], len(result["releases"])), (0, True, 8))
        self.assertEqual({row["code"] for row in result["releases"]},
                         {"OK", "PREFLIGHT_INVALID_BIND", "CHECKSUM_MISMATCH"})

    def test_launcher_sources_use_repo_location_and_preserve_argument_as_data(self):
        for name in ("deploy", "status", "doctor", "demo"):
            with self.subTest(wrapper=name):
                bash = (REPO / f"scripts/linux/{name}.sh").read_text(encoding="utf-8")
                shell = (REPO / f"scripts/windows/{name}.ps1").read_text(encoding="utf-8")
                self.assertIn('"$(dirname -- "${BASH_SOURCE[0]}")/../.."', bash)
                self.assertIn(f"exec python3 -B -m deploysafe {name}", bash)
                self.assertIn("Join-Path $PSScriptRoot '../..'", shell)
                self.assertIn("exit $Code", shell)
        self.assertIn('--release "$1"', (REPO / "scripts/linux/deploy.sh").read_text(encoding="utf-8"))
        self.assertIn("'--release', $Release", (REPO / "scripts/windows/deploy.ps1").read_text(encoding="utf-8"))


class DashboardTests(unittest.TestCase):
    def test_render_escapes_audit_content(self):
        with workspace() as policy, Store(policy.workspace) as store:
            store.event(None, None, "TEST", "FAIL", "SCRIPT", '<script>alert("x")</script>')
            page = dashboard.render(policy)
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", page)

    def test_dashboard_port_must_be_distinct_from_service_ports(self):
        with workspace() as policy, self.assertRaisesRegex(Exception, "separate port"):
            dashboard.server(policy, policy.active_port)

    def test_http_dashboard_serves_real_history_and_refuses_mutation(self):
        with workspace((8934, 8935, 8936)) as policy, Store(policy.workspace) as store:
            store.event(None, None, "RECOVERY", "PASS", "LOCAL_EVIDENCE", "Checked prior release")
            with dashboard.server(policy, policy.dashboard_port) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    url = f"http://127.0.0.1:{policy.dashboard_port}"
                    opener = build_opener(ProxyHandler({}))
                    with opener.open(url, timeout=2) as response:
                        self.assertIn("LOCAL_EVIDENCE", response.read().decode())
                        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
                    for request, code in ((url + "/deploy", 404),
                                          (Request(url, method="POST", data=b"deploy"), 405),
                                          (Request(url, headers={"Host": "external.invalid"}), 400)):
                        with self.assertRaises(HTTPError) as error:
                            opener.open(request, timeout=2)
                        self.assertEqual(error.exception.code, code)
                        error.exception.close()
                    self.assertEqual(len(store.history(10)["events"]), 1)
                finally:
                    server.shutdown()
                    worker.join(timeout=2)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.policy = self.enterContext(workspace((8951, 8952, 8953)))
        self.store = self.enterContext(Store(self.policy.workspace))
        self.manager = Manager(self.policy, self.store)
        self.manager.reconcile()
        self.addCleanup(self.manager.close)

    def test_two_real_upgrades_verify_health_and_retire_only_previous_child(self):
        first = self.manager.deploy("1.0.0")
        self.assertEqual((first.exit_code, first.active_version), (0, "1.0.0"))
        original = self.manager.active_child
        self.assertTrue(health.probe(original.port, "/version", "1.0.0", .5).ok)
        second = self.manager.deploy("1.1.0")
        self.assertEqual((second.exit_code, second.active_version), (0, "1.1.0"))
        self.assertFalse(original.alive)
        process_info = read_json(self.policy.workspace,
                                 f"runtime/{original.instance}.process.json")
        self.assertEqual(process_info["release_id"], "1.0.0")
        self.assertIn("exited_at", process_info)
        self.assertTrue(health.probe(self.policy.candidate_port, "/health", "1.1.0", .5).ok)

    def test_real_503_and_wrong_version_preserve_same_live_prior_process(self):
        baseline = self.manager.deploy("1.1.0")
        self.assertEqual(baseline.exit_code, 0)
        prior = self.manager.active_child
        for version, code in (("1.2.0-healthfail", "HTTP_503"), ("1.7.0-wrongversion", "WRONG_VERSION")):
            with self.subTest(version=version):
                result = self.manager.deploy(version)
                self.assertEqual((result.code, result.state, result.active_version), (code, "ROLLED_BACK", "1.1.0"))
                self.assertIs(prior, self.manager.active_child)
                self.assertTrue(health.probe(prior.port, "/version", "1.1.0", .5).ok)
                self.assertEqual(set(self.manager.children), {prior.instance})

    def test_real_timeout_rejects_candidate_with_bounded_attempts(self):
        self.assertEqual(self.manager.deploy("1.1.0").exit_code, 0)
        started = time.monotonic()
        result = self.manager.deploy("1.4.0-timeout")
        self.assertEqual((result.code, result.state), ("HEALTH_TIMEOUT", "ROLLED_BACK"))
        checks = self.store.rows("SELECT code FROM health_checks WHERE run_id=? AND phase='candidate'", (result.run_id,))
        self.assertEqual([item["code"] for item in checks], ["HEALTH_TIMEOUT"] * 3)
        self.assertLess(time.monotonic() - started, 8)
        processes.check_port(self.policy.candidate_port)

    def test_real_startup_exception_creates_log_and_cleans_port(self):
        result = self.manager.deploy("1.6.0-crash")
        self.assertEqual((result.code, result.state), ("PROCESS_EXITED", "FAILED"))
        logs = list((self.policy.workspace / "logs").glob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("Controlled demo startup failure", logs[0].read_text(encoding="ascii"))
        self.assertEqual(self.manager.children, {})
        processes.check_port(self.policy.active_port)

    def test_real_collision_retains_prior_and_leaves_unrelated_socket_owned_by_test(self):
        self.assertEqual(self.manager.deploy("1.0.0").exit_code, 0)
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", self.policy.candidate_port))
            blocker.listen()
            result = self.manager.deploy("1.1.0")
            self.assertEqual((result.code, result.state), ("PORT_IN_USE", "ROLLED_BACK"))
            with socket.create_connection(blocker.getsockname(), timeout=1):
                connection, _ = blocker.accept()
                connection.close()
        self.assertTrue(self.manager.status()["live"])


class SupervisorIntegrationTests(unittest.TestCase):
    def exec(self, policy, *arguments):
        result = subprocess.run([sys.executable, "-B", "-m", "deploysafe",
                                 "--workspace", str(policy.workspace), *arguments, "--json"],
                                cwd=REPO, capture_output=True, text=True, timeout=30)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_cli_owns_service_across_processes_and_shutdown_keeps_history(self):
        with workspace((8961, 8962, 8963)) as policy:
            try:
                code, deployed = self.exec(policy, "deploy", "--release", "1.0.0")
                self.assertEqual(code, 0)
                code, status = self.exec(policy, "status")
                self.assertEqual((code, status["live"]), (0, True))
                self.assertEqual(status["active"]["run_id"], deployed["run_id"])
                self.assertTrue(health.probe(policy.active_port, "/health", "1.0.0", .5).ok)
            finally:
                self.assertEqual(self.exec(policy, "stop-demo")[0], 0)
            self.assertEqual(self.exec(policy, "status")[1]["active"], None)
            self.assertEqual(self.exec(policy, "history")[1]["deployments"][0]["state"], "ACTIVE")
            processes.check_port(policy.active_port)

    def test_supervisor_abrupt_exit_stops_guardian_and_recovers_authoritative_state(self):
        with workspace((8971, 8972, 8973)) as policy:
            with Store(policy.workspace):
                pass
            supervisor_process = supervisor._spawn(policy)
            try:
                ready = supervisor._server(policy, start=False) if supervisor.held(policy.workspace, "supervisor") else None
                if ready is None:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline and supervisor._lease(policy) is None:
                        self.assertIsNone(supervisor_process.poll())
                        time.sleep(.05)
                self.assertIsNotNone(supervisor._lease(policy))
                deployed = supervisor.request(policy, "deploy", "1.0.0")
                self.assertEqual(deployed["exit_code"], 0)
                supervisor_process.terminate()
                supervisor_process.wait(timeout=6)
                atomic_json(policy.workspace, POINTER, None)
                self.assertFalse(self.exec(policy, "status")[1]["live"])
                code, result = self.exec(policy, "recover")
                self.assertEqual((code, result["active_version"]), (0, "1.0.0"))
                self.assertEqual(read_json(policy.workspace, POINTER)["run_id"], deployed["run_id"])
                self.assertTrue(health.probe(policy.active_port, "/health", "1.0.0", .5).ok)
                self.assertIn("POINTER_RECONCILED", [row["code"] for row in self.exec(policy, "history")[1]["events"]])
                registry = [read_json(policy.workspace, entry) for entry in
                            (policy.workspace / "runtime").glob("*.process.json")]
                self.assertEqual(len(registry), 2)
                self.assertEqual(sum("exited_at" in entry for entry in registry), 1)
            finally:
                if supervisor_process.poll() is None:
                    supervisor_process.terminate()
                    supervisor_process.wait(timeout=6)
                self.assertEqual(self.exec(policy, "stop-demo")[0], 0)
            processes.check_port(policy.active_port)

    def test_demo_single_scenario_bootstraps_and_persists_real_checks(self):
        with workspace() as policy:
            run = demo.run_demo(policy, "03_health_503")
            self.assertEqual((run["passed"], run["total"]), (1, 1))
            report = read_json(policy.workspace, run["scenarios"][0]["report"], 65536)
            self.assertEqual(report["observed"]["result"]["active_version"], "1.1.0")
            self.assertEqual(report["observed"]["failure_code"], "HTTP_503")
            self.assertIn("rollback", {row["phase"] for row in report["health_checks"]})
            for port in (8865, 8866):
                processes.check_port(port)
