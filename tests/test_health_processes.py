import io
import os
import socket
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

from deploysafe import health, processes, releases
from deploysafe.locking import WorkspaceLock, held
from deploysafe.models import DeployError
from deploysafe.security import read_json

from support import workspace


class HealthTests(unittest.TestCase):
    def probe(self, content=b'{"status":"ok","version":"1.0.0"}', status=200, error=None, endpoint="/health"):
        response = Mock(status=status)
        response.read.return_value = content
        opener = MagicMock()
        opener.open.side_effect = error
        opener.open.return_value.__enter__.return_value = response
        with patch("deploysafe.health.build_opener", return_value=opener) as builder:
            result = health.probe(8765, endpoint, "1.0.0", .15)
        return result, opener, builder

    def test_success_uses_fixed_address_and_disables_proxies(self):
        result, opener, builder = self.probe(endpoint="/version")
        self.assertTrue(result.ok)
        opener.open.assert_called_once_with("http://127.0.0.1:8765/version", timeout=.15)
        self.assertEqual(builder.call_args.args[0].proxies, {})
        self.assertIsNone(builder.call_args.args[1].redirect_request())

    def test_503_has_stable_code_and_closes_response(self):
        body = io.BytesIO()
        error = HTTPError("http://127.0.0.1:8765/health", 503, "oops", {}, body)
        result, *_ = self.probe(error=error)
        self.assertEqual((result.code, result.http_status), ("HTTP_503", 503))
        self.assertTrue(body.closed)

    def test_non_200_2xx_is_rejected(self):
        result, *_ = self.probe(status=204)
        self.assertEqual(result.code, "HTTP_204")

    def test_timeout_and_refusal_distinguished(self):
        for error, code in ((TimeoutError(), "HEALTH_TIMEOUT"),
                            (URLError(ConnectionRefusedError()), "CONNECTION_REFUSED")):
            with self.subTest(code=code):
                result, *_ = self.probe(error=error)
                self.assertEqual(result.code, code)

    def test_malformed_oversize_and_duplicate_json_rejected(self):
        for body in (b"<div>", b"x" * (health.MAX_BODY + 1),
                     b'{"status":"ok","status":"ok","version":"1.0.0"}'):
            with self.subTest(size=len(body)):
                result, *_ = self.probe(content=body)
                self.assertEqual(result.code, "MALFORMED_JSON")

    def test_wrong_version_and_schema_are_reported_separately(self):
        for body, code in ((b'{"status":"ok","version":"2.0.0"}', "WRONG_VERSION"),
                           (b'{"status":"ok","version":1}', "INVALID_HEALTH"),
                           (b'{"status":"ok","version":"1.0.0","extra":0}', "INVALID_HEALTH")):
            with self.subTest(body=body):
                result, *_ = self.probe(content=body)
                self.assertEqual(result.code, code)

    def test_rejects_arbitrary_endpoint_before_http(self):
        with patch("deploysafe.health.build_opener") as build:
            with self.assertRaises(DeployError):
                health.probe(8765, "//external.invalid", "1.0.0", .15)
            build.assert_not_called()

    def test_retry_attempts_and_backoff_bounded(self):
        with workspace() as policy:
            child = SimpleNamespace(port=8765, version="1.0.0", alive=True)
            record = Mock()
            with patch("deploysafe.health.probe", return_value=health.Check("/health", "HTTP_503", "bad", 0)) as probe:
                with patch("deploysafe.health.time.sleep") as sleep:
                    result = health.verify(policy, child, record, "candidate")
            self.assertEqual(result.code, "HTTP_503")
            self.assertEqual(probe.call_count, policy.health_attempts)
            self.assertEqual(sleep.call_count, policy.health_attempts - 1)
            self.assertEqual([call.args[1] for call in record.call_args_list], [1, 2, 3])

    def test_checks_version_endpoint_after_health(self):
        with workspace() as policy:
            child = SimpleNamespace(port=8765, version="1.0.0", alive=True)
            with patch("deploysafe.health.probe", side_effect=[
                health.Check("/health", "OK", "", 0), health.Check("/version", "WRONG_VERSION", "", 0)
            ]) as probe:
                result = health.verify(policy, child, Mock(), "candidate", attempts=1)
            self.assertEqual(result.code, "WRONG_VERSION")
            self.assertEqual([call.args[1] for call in probe.call_args_list], ["/health", "/version"])

    def test_dead_process_is_never_probed(self):
        with workspace() as policy, patch("deploysafe.health.probe") as probe:
            result = health.verify(policy, SimpleNamespace(alive=False), Mock(), "candidate")
            self.assertEqual(result.code, "PROCESS_EXITED")
            probe.assert_not_called()


class ProcessTests(unittest.TestCase):
    def test_collision_is_detected_on_explicit_allowed_port(self):
        port = 8933
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen()
            with self.assertRaises(DeployError) as raised:
                processes.check_port(port)
            self.assertEqual(raised.exception.code, "PORT_IN_USE")
        processes.check_port(port)

    def test_inherited_environment_excludes_injection_and_credentials(self):
        with patch.dict(os.environ, {"PYTHONPATH": "bad", "HTTPS_PROXY": "bad", "SECRET": "test",
                                     "SYSTEMROOT": "preserved-on-windows"}, clear=True):
            result = processes.environment()
        self.assertEqual(result, {"SYSTEMROOT": "preserved-on-windows"} if os.name == "nt" else {})

    def test_guard_launch_uses_list_isolated_interpreter_and_no_shell(self):
        with workspace() as policy:
            source = releases.bundled(policy, "1.0.0")
            run, instance = "a" * 32, "b" * 32
            staged = releases.stage(policy, source, run)
            with patch("deploysafe.processes.check_port"), patch("deploysafe.processes.subprocess.Popen") as popen:
                child = processes.launch(policy, run, source.version, source.checksum, 8765, instance)
            args, options = popen.call_args
            self.assertEqual(args[0][1:4], ["-I", "-B", "-u"])
            self.assertEqual(args[0][-4:], [source.version, source.checksum, "8765", instance])
            self.assertIs(options["shell"], False)
            self.assertEqual(options["cwd"], staged.path)
            self.assertEqual(child.instance, instance)

    def test_log_capture_discards_control_bytes_and_caps_output(self):
        out = io.BytesIO()
        processes.capture(io.BytesIO(b"hello\x1b\x00\n" + b"x" * (processes.LOG_LIMIT + 100)), out)
        self.assertEqual(len(out.getvalue()), processes.LOG_LIMIT)
        self.assertTrue(out.getvalue().startswith(b"hello\n"))
        self.assertNotIn(b"\x1b", out.getvalue())

    def test_termination_uses_only_owned_handle_and_bounded_fallback(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("owned", 1), 7]
        self.assertEqual(processes.stop_owned(child), 7)
        child.terminate.assert_called_once_with()
        child.kill.assert_called_once_with()
        self.assertEqual([call.kwargs["timeout"] for call in child.wait.call_args_list], [1, 1])

    def test_guardian_stop_closes_lifeline_and_does_not_signal(self):
        proc = Mock()
        proc.stdin = io.BytesIO()
        remote = processes.Child(proc, "1.0.0", 8765, "a" * 32, "b" * 32)
        remote.stop()
        self.assertTrue(proc.stdin.closed)
        proc.wait.assert_called_once_with(timeout=5)
        proc.kill.assert_not_called()
        proc.terminate.assert_not_called()

    def test_lock_never_replaces_live_owner(self):
        with workspace() as policy, WorkspaceLock(policy.workspace, "operation") as first:
            self.assertTrue(held(policy.workspace, "operation"))
            with self.assertRaises(DeployError) as raised, WorkspaceLock(policy.workspace, "operation"):
                self.fail("duplicate lock owner")
            self.assertEqual(raised.exception.exit_code, 6)
            self.assertEqual(read_json(policy.workspace, first.owner)["token"], first.token)

    def test_stale_marker_cleared_only_under_free_kernel_guard(self):
        with workspace() as policy:
            with WorkspaceLock(policy.workspace, "operation") as first:
                stale = first.owner.read_bytes()
            first.owner.write_bytes(stale)
            with WorkspaceLock(policy.workspace, "operation") as new:
                self.assertNotEqual(new.token, first.token)
                self.assertEqual(read_json(policy.workspace, new.owner)["token"], new.token)
            self.assertFalse(first.owner.exists())
