"""Protocol contract tests, independent of the real log reader and state store."""
from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from panel.protocol import INSTRUCTIONS, RESOURCE, PanelAPI, definitions, loads_strict, serve_stdio


ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(tempfile.gettempdir()) / "codex-execution-panel"
SID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


class FakeService:
    def __init__(self):
        self.calls = []

    def validate_session(self, sid):
        return sid in (SID, OTHER)

    def list_sessions(self):
        self.calls.append(("list",))
        return [{"id": SID, "title": "Synthetic task"}, {"id": OTHER, "title": "Other synthetic task"}]

    def snapshot(self, sid):
        self.calls.append(("snapshot", sid))
        return {"sessionId": sid, "revision": "revision-1", "runtime": {"status": "working"}, "nodes": []}

    def set_plan(self, sid, title, nodes, reason):
        self.calls.append(("set", sid, title, nodes, reason))
        return {"sessionId": sid, "saved": True}

    def update_node(self, sid, node_id, status, summary, issue, **extra):
        self.calls.append(("update", sid, node_id, status, summary, issue))
        self.update_extra = extra
        return {"sessionId": sid, "saved": True}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.api = PanelAPI(self.service, ROOT)

    def tool_call(self, name, args, meta=None):
        return self.api.dispatch("tools/call", {"name": name, "arguments": args, "_meta": meta or {}})

    def exchange(self, messages):
        encoded = b"".join((json.dumps(m, separators=(",", ":")) + "\n").encode() for m in messages)
        sink = io.BytesIO()
        serve_stdio(self.api, io.BytesIO(encoded), sink)
        return [json.loads(line) for line in sink.getvalue().splitlines()]

    def test_native_entrypoint_and_private_refresh_tools(self):
        tools = {t["name"]: t for t in definitions()}
        entry = tools["open_execution_panel"]
        self.assertEqual(entry["_meta"]["openai/ui"]["entrypoints"], [{"type": "thread"}])
        self.assertEqual(entry["_meta"]["ui"]["resourceUri"], RESOURCE)
        self.assertTrue(entry["annotations"]["readOnlyHint"])
        for name in ("open_execution_panel", "get_execution_panel_update", "list_execution_sessions"):
            self.assertEqual(tools[name]["_meta"]["ui"]["visibility"], ["app"])
        for name in ("set_execution_plan", "report_execution_update"):
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[name]["annotations"]["destructiveHint"])
        self.assertIn("Never", INSTRUCTIONS)
        self.assertNotIn("turn/start", INSTRUCTIONS)

    def test_host_aliases_bind_only_verified_current_task(self):
        for meta in ({"thread_id": SID}, {"threadId": SID}, {"thread_id": SID, "threadId": SID}):
            with self.subTest(meta=meta):
                result = self.api.call("open_execution_panel", {}, meta)["structuredContent"]
                self.assertEqual(result["sessionId"], SID)

    def test_missing_invalid_and_conflicting_native_identity_offer_selector(self):
        cases = ({}, {"thread_id": "unknown"}, {"thread_id": 3}, {"thread_id": SID, "threadId": OTHER},
                 {"thread_id": ""})
        for meta in cases:
            with self.subTest(meta=meta):
                self.service.calls.clear()
                data = self.api.call("open_execution_panel", {}, meta)["structuredContent"]
                self.assertEqual(data["viewerState"], "select-session")
                self.assertFalse(any(call[0] == "snapshot" for call in self.service.calls))

    def test_explicit_selection_reads_exact_task_without_host_identity(self):
        data = self.api.call("show_execution_panel", {"sessionId": OTHER})["structuredContent"]
        self.assertEqual(data["sessionId"], OTHER)
        read = self.api.call("get_execution_panel_update", {"sessionId": OTHER})["structuredContent"]
        self.assertEqual(read["sessionId"], OTHER)
        unchanged = self.api.call("get_execution_panel_update", {"sessionId": OTHER, "revision": "revision-1"})["structuredContent"]
        self.assertTrue(unchanged["unchanged"])
        self.assertEqual(unchanged["runtime"], {"status": "working"})

    def test_write_rejects_missing_mismatched_or_conflicting_context(self):
        plan = {"title": "Synthetic plan", "nodes": [{"id": "one", "title": "One"}]}
        cases = ((plan, {}), ({**plan, "sessionId": OTHER}, {"thread_id": SID}),
                 (plan, {"thread_id": SID, "threadId": OTHER}), (plan, {"thread_id": 1}))
        for args, meta in cases:
            with self.subTest(args=args, meta=meta):
                result = self.tool_call("set_execution_plan", args, meta)
                self.assertTrue(result["isError"])
        self.assertFalse(any(call[0] == "set" for call in self.service.calls))

    def test_explicit_mismatch_does_not_open_another_task(self):
        result = self.api.call("show_execution_panel", {"sessionId": OTHER}, {"thread_id": SID})["structuredContent"]
        self.assertEqual(result["viewerState"], "select-session")

    def test_valid_writes_only_call_local_service(self):
        plan = {"title": "Synthetic plan", "nodes": [{"id": "one", "title": "One"}]}
        self.assertNotIn("isError", self.tool_call("set_execution_plan", plan, {"thread_id": SID}))
        issue = {"id": "choice", "kind": "branch", "title": "Route changed", "status": "open",
                 "options": ["A", "B"], "selected": "A"}
        self.assertNotIn("isError", self.tool_call("report_execution_update", {"nodeId": "one", "status": "in_progress", "issue": issue}, {"threadId": SID}))
        self.assertEqual([call[0] for call in self.service.calls], ["set", "update"])
        self.assertEqual(self.service.calls[1][-1], issue)

    def test_schema_rejects_unknown_fields_types_and_invalid_enums(self):
        cases = [
            ("open_execution_panel", {"sessionId": SID}),
            ("get_execution_panel_update", {}),
            ("get_execution_panel_update", {"sessionId": 1}),
            ("get_execution_panel_update", {"sessionId": SID, "extra": True}),
            ("get_execution_panel_update", {"sessionId": "unknown"}),
            ("set_execution_plan", {"sessionId": SID, "title": "x", "nodes": []}),
            ("set_execution_plan", {"sessionId": SID, "title": "x", "nodes": [{"id": "x", "title": "x", "hidden": 1}]}),
            ("set_execution_plan", {"sessionId": SID, "title": False, "nodes": [{"id": "x", "title": "x"}]}),
            ("report_execution_update", {"sessionId": SID, "nodeId": "one", "status": "success"}),
            ("report_execution_update", {"sessionId": SID, "nodeId": "one", "issue": {"id": "x", "kind": "branch", "title": "x", "status": "open", "options": [1]}}),
            ("report_execution_update", {"sessionId": SID, "nodeId": "x" * 4001}),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                self.assertTrue(self.tool_call(name, args).get("isError"))
        self.assertEqual(self.service.calls, [])

    def test_verification_issue_contract_accepts_kind_and_forwards_reclassification_context(self):
        issue = {"id": "native-acceptance", "kind": "verification", "title": "Native draft acceptance", "status": "open"}
        result = self.tool_call("report_execution_update", {
            "nodeId": "one", "issue": issue, "reason": "Correct a pending acceptance state", "expectedVersion": 4,
        }, {"thread_id": SID})
        self.assertNotIn("isError", result)
        self.assertEqual(self.service.calls[-1][-1], issue)
        self.assertEqual(self.service.update_extra, {
            "reason": "Correct a pending acceptance state", "expected_version": 4,
        })
        invalid = self.tool_call("report_execution_update", {"nodeId": "one", "issue": {**issue, "kind": "review"}}, {"thread_id": SID})
        self.assertTrue(invalid["isError"])

    def test_handshake_gates_tools_and_notifications_have_no_response(self):
        messages = [
            {"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 99}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 5, "method": "unknown/method"},
        ]
        responses = self.exchange(messages)
        self.assertEqual([r["id"] for r in responses], [0, 1, 2, 3, 4, 5])
        self.assertIn("error", responses[0])
        self.assertIn("error", responses[2])
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("instructions", responses[1]["result"])
        self.assertEqual(responses[3]["result"], {})
        names = {tool["name"] for tool in responses[4]["result"]["tools"]}
        self.assertTrue({"submit_execution_feedback", "confirm_execution_feedback_delivery", "read_execution_plan", "resolve_execution_request"} <= names)
        self.assertFalse(any("goal" in name or "interrupt" in name for name in names))
        self.assertEqual(responses[5]["error"]["code"], -32601)
        self.assertFalse(any("method" in response for response in responses))

    def test_invalid_notification_params_never_emit_a_response(self):
        responses = self.exchange([{"jsonrpc": "2.0", "method": "ping", "params": []}])
        self.assertEqual(responses, [])

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                loads_strict(raw)

    def test_resource_assembler_inlines_assets_with_closed_csp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT, prefix="protocol-assets-") as folder:
            fixture = Path(folder)
            assets = fixture / "assets"
            assets.mkdir()
            (assets / "panel.html").write_text('<main>Execution</main><link rel="stylesheet" href="panel.css"><script src="panel-theme.js"></script><script src="mcp-app-bridge.js"></script><script src="panel.js"></script>', encoding="utf-8")
            (assets / "panel.css").write_text("main { color: inherit; }", encoding="utf-8")
            (assets / "panel-theme.js").write_text("window.themeLoaded=true;", encoding="utf-8")
            (assets / "mcp-app-bridge.js").write_text("window.bridgeVersion='__TRAJECTORY_VERSION__';", encoding="utf-8")
            (assets / "plan-model.js").write_text("window.modelLoaded=true;", encoding="utf-8")
            (assets / "panel.js").write_text("window.example='safe';", encoding="utf-8")
            resource = PanelAPI(self.service, fixture).resource()["contents"][0]
            self.assertEqual(resource["uri"], RESOURCE)
            self.assertEqual(resource["mimeType"], "text/html;profile=mcp-app")
            self.assertEqual(resource["_meta"]["ui"]["csp"], {"connectDomains": [], "resourceDomains": []})
            self.assertEqual(resource["_meta"]["openai/ui"], {
                "availableDisplayModes": ["fullscreen"],
                "preferredDisplayMode": "fullscreen",
            })
            self.assertNotRegex(resource["text"], r'<script\b[^>]*\bsrc\s*=|<link\b[^>]*\bhref\s*=')
            self.assertNotIn("__TRAJECTORY_VERSION__", resource["text"])
            self.assertIn("<style>", resource["text"])

    def test_actual_ui_is_self_contained_and_has_no_goal_or_turn_control(self):
        required = [ROOT / "assets" / name for name in ("panel.html", "panel.css", "panel.js", "panel-theme.js", "mcp-app-bridge.js")]
        if not all(path.exists() for path in required):
            self.skipTest("Actual UI files are still being authored")
        html = self.api.resource()["contents"][0]["text"]
        self.assertNotRegex(html, r'<script\b[^>]*\bsrc\s*=|<link\b[^>]*\bhref\s*=')
        self.assertNotRegex(html, r'(?:src|href)\s*=\s*[\"\']https?://|url\(\s*[\"\']?https?://')
        self.assertNotRegex(html, r'<(?:form|textarea)\b')
        for forbidden in ('"turn/start"', '"turn/steer"', '"turn/interrupt"', '"thread/goal/set"', 'submit_execution_feedback', 'sendFollowUpMessage'):
            self.assertFalse(forbidden in html, forbidden)

    def test_viewer_binding_requires_matching_verified_host_identity(self):
        for meta, verified in (({}, False), ({"thread_id": SID}, True), ({"thread_id": OTHER}, False),
                               ({"thread_id": SID, "threadId": OTHER}, False)):
            data = self.tool_call("get_execution_panel_update", {"sessionId": SID}, meta)["structuredContent"]
            self.assertEqual(data["viewerBinding"], {"sessionId": SID, "verified": verified})


if __name__ == "__main__":
    unittest.main()
