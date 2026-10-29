"""Exercise the real local preview endpoint against synthetic sessions only."""

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from panel.state import PanelService


ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(tempfile.gettempdir()) / "codex-execution-panel"
SESSION = "feedback-http-test"
TOKEN = "local-preview-test-token-not-a-real-user-secret"


class FeedbackHTTPTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codex_home = self.root / "codex"
        log_dir = self.codex_home / "sessions"
        log_dir.mkdir(parents=True)
        (log_dir / f"rollout-test-{SESSION}.jsonl").write_text(json.dumps({
            "type": "session_meta", "payload": {"id": SESSION}}) + "\n", encoding="utf-8")
        self.service = PanelService(self.root / "state", self.codex_home)
        self.service.set_plan(SESSION, "Preview HTTP test", [{"id": "build", "title": "Build fixture", "status": "in_progress"}])
        self.token_file = self.root / "write-token.txt"
        self.token_file.write_text(TOKEN, encoding="utf-8")
        self.port = self.start_server(with_token=True)

    def start_server(self, with_token):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        command = [sys.executable, "-B", "-u", str(ROOT / "server.py"), "preview", "--port", str(port),
                   "--state-dir", str(self.root / "state"), "--codex-home", str(self.codex_home)]
        if with_token:
            command += ["--write-token-file", str(self.token_file)]
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TEMP=str(self.root), TMP=str(self.root))
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.addCleanup(self.stop_server, process)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail("Synthetic preview server failed to start")
            try:
                status, _ = self.request("GET", f"/api/state?sessionId={SESSION}", port=port)
                if status == 200:
                    return port
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.02)
        self.fail("Synthetic preview server did not become ready")

    @staticmethod
    def stop_server(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    def request(self, method, path, payload=None, headers=None, *, port=None):
        selected_port = port or self.port
        body = json.dumps(payload).encode("utf-8") if isinstance(payload, (dict, list)) else payload
        connection = http.client.HTTPConnection("127.0.0.1", selected_port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            body = response.read()
            mime = response.getheader("Content-Type", "")
            value = json.loads(body) if "application/json" in mime else body.decode("utf-8", errors="replace")
            return response.status, value
        finally:
            connection.close()

    def payload(self, request_id="http-feedback"):
        return {"sessionId": SESSION, "nodeId": "build", "kind": "modify", "message": "Keep the fixture API", "requestId": request_id}

    def headers(self, **overrides):
        headers = {"Content-Type": "application/json", "X-Execution-Panel-Token": TOKEN,
                   "Origin": f"http://127.0.0.1:{self.port}"}
        headers.update(overrides)
        return headers

    def assert_no_feedback(self):
        self.assertEqual(self.service.snapshot(SESSION)["requests"], [])

    def test_missing_or_wrong_token_rejects_writes(self):
        missing = self.headers()
        missing.pop("X-Execution-Panel-Token")
        wrong = self.headers(**{"X-Execution-Panel-Token": "wrong-token"})
        for headers in (missing, wrong):
            status, _ = self.request("POST", "/api/feedback", self.payload(), headers)
            self.assertEqual(status, 403)
        self.assert_no_feedback()

    def test_wrong_origin_or_host_rejects_even_a_correct_token(self):
        for headers in (self.headers(Origin="https://untrusted.example"), self.headers(Host="untrusted.example")):
            status, _ = self.request("POST", "/api/feedback", self.payload(), headers)
            self.assertEqual(status, 403)
        self.assert_no_feedback()

    def test_content_type_and_body_limit_are_enforced(self):
        status, _ = self.request("POST", "/api/feedback", self.payload(), self.headers(**{"Content-Type": "text/plain"}))
        self.assertEqual(status, 400)
        oversized = self.headers(**{"Content-Length": "65537"})
        status, _ = self.request("POST", "/api/feedback", b"{}", oversized)
        self.assertEqual(status, 400)
        self.assert_no_feedback()

    def test_correct_token_only_records_pending_without_chat_handoff(self):
        status, value = self.request("POST", "/api/feedback", self.payload(), self.headers())
        self.assertEqual(status, 200)
        self.assertNotIn("handoff", value)
        self.assertEqual(value["delivery"]["status"], "pending")
        self.assertEqual(value["requests"][0]["status"], "pending")
        self.assertFalse(value["host"]["available"])
        self.assertNotEqual(value["runtime"]["status"], "paused")
        persisted = self.service.snapshot(SESSION)
        self.assertEqual(len(persisted["requests"]), 1)
        for _ in range(2):
            status, polled = self.request("GET", f"/api/state?sessionId={SESSION}")
            self.assertEqual(status, 200)
            self.assertNotIn("handoff", polled)
            self.assertEqual(polled["requests"][0]["delivery"]["status"], "pending")
            self.assertNotIn(TOKEN, json.dumps(polled))
        duplicate_status, duplicate = self.request("POST", "/api/feedback", self.payload(), self.headers())
        self.assertEqual(duplicate_status, 200)
        self.assertNotIn("handoff", duplicate)
        self.assertEqual(len(duplicate["requests"]), 1)

    def test_invalid_json_or_unknown_fields_cannot_write_a_request(self):
        status, _ = self.request("POST", "/api/feedback", b"{invalid", self.headers())
        self.assertEqual(status, 400)
        malformed = self.payload()
        malformed["goalStatus"] = "paused"
        status, _ = self.request("POST", "/api/feedback", malformed, self.headers())
        self.assertEqual(status, 400)
        self.assert_no_feedback()

    def test_preview_without_enabled_write_token_remains_read_only(self):
        readonly_port = self.start_server(with_token=False)
        headers = self.headers(Origin=f"http://127.0.0.1:{readonly_port}")
        status, _ = self.request("POST", "/api/feedback", self.payload(), headers, port=readonly_port)
        self.assertEqual(status, 403)
        self.assert_no_feedback()


if __name__ == "__main__":
    unittest.main()
