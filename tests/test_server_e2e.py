"""Launch the real stdio server against a synthetic local task, not a host mock."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TASK = "11111111-1111-4111-8111-111111111111"


class ServerEndToEndTests(unittest.TestCase):
    def test_actual_process_persists_plan_and_serves_inline_ui(self):
        with tempfile.TemporaryDirectory(prefix="execution-panel-e2e-") as temp:
            base = Path(temp)
            sessions = base / "codex/sessions"
            sessions.mkdir(parents=True)
            rollout = sessions / f"rollout-demo-{TASK}.jsonl"
            rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": TASK}}) + "\n", encoding="utf-8")
            environment = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TEMP=temp, TMP=temp)
            process = subprocess.Popen([sys.executable, "-B", "-u", str(ROOT / "server.py"), "stdio",
                                        "--state-dir", str(base / "state"), "--codex-home", str(base / "codex")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
            def send(value):
                process.stdin.write((json.dumps(value) + "\n").encode())
                process.stdin.flush()
            def call(identifier, method, params=None):
                send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}})
                response = json.loads(process.stdout.readline())
                self.assertEqual(response["id"], identifier)
                self.assertNotIn("error", response)
                return response["result"]
            try:
                initialized = call(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
                self.assertIn("instructions", initialized)
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                value = call(2, "tools/call", {"name": "set_execution_plan", "_meta": {"thread_id": TASK}, "arguments": {
                    "title": "测试执行计划", "nodes": [{"id": "build", "title": "实现功能", "status": "in_progress"}, {"id": "check", "title": "验证结果"}]}})
                self.assertFalse(value.get("isError", False))
                self.assertEqual(value["structuredContent"]["progress"]["percent"], 0)
                value = call(3, "tools/call", {"name": "report_execution_update", "arguments": {"sessionId": TASK, "nodeId": "build", "status": "completed"}})
                self.assertEqual(value["structuredContent"]["progress"]["percent"], 50)
                current = call(4, "tools/call", {"name": "open_execution_panel", "arguments": {}, "_meta": {"threadId": TASK}})
                self.assertEqual(current["structuredContent"]["sessionId"], TASK)
                revision = current["structuredContent"]["revision"]
                unchanged = call(5, "tools/call", {"name": "get_execution_panel_update", "arguments": {"sessionId": TASK, "revision": revision}})
                self.assertTrue(unchanged["structuredContent"]["unchanged"])
                resource = call(6, "resources/read", {"uri": "ui://codex-execution-panel/panel.html"})
                self.assertIn("text/html;profile=mcp-app", resource["contents"][0]["mimeType"])
                self.assertNotIn('src="panel.js"', resource["contents"][0]["text"])
            finally:
                process.stdin.close()
                process.wait(timeout=10)
                errors = process.stderr.read().decode()
                process.stdout.close()
                process.stderr.close()
                self.assertEqual(process.returncode, 0, errors)
                self.assertEqual(errors, "")
            # A second process/service must see the same stored business plan.
            completed = subprocess.run([sys.executable, "-B", str(ROOT / "server.py"), "snapshot", "--session-id", TASK,
                                        "--state-dir", str(base / "state"), "--codex-home", str(base / "codex")],
                                       capture_output=True, check=True, env=environment)
            self.assertEqual(json.loads(completed.stdout)["progress"]["percent"], 50)


if __name__ == "__main__":
    unittest.main()
