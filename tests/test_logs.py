"""Evidence-bound execution parsing, source isolation and append safety."""

import json
from pathlib import Path
import tempfile
import unittest

from panel.logs import CodexLogReader


TEMP_ROOT = Path(tempfile.gettempdir()) / "codex-execution-panel"


def event(kind, payload, ordinal=None):
    value = {"timestamp": "2026-10-27T08:00:00Z", "type": kind, "payload": payload}
    if ordinal is not None:
        value["ordinal"] = ordinal
    return value


def call(call_id, name, arguments):
    return event("response_item", {"type": "function_call", "name": name,
                                  "call_id": call_id, "arguments": json.dumps(arguments)})


def output(call_id, value="Plan updated"):
    return event("response_item", {"type": "function_call_output", "call_id": call_id, "output": value})


class LogTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.home = Path(self.temp.name) / "codex"
        self.root = self.home / "sessions"
        self.root.mkdir(parents=True)
        self.path = self.root / "rollout-2026-session-1.jsonl"
        self.meta = event("session_meta", {"id": "session-1", "title": "Sample"})
        self.write(self.meta)
        self.reader = CodexLogReader(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, *entries):
        self.path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def append(self, *entries):
        with self.path.open("a", encoding="utf-8") as stream:
            for entry in entries:
                stream.write(json.dumps(entry) + "\n")

    def test_filename_and_prefix_are_not_identity(self):
        self.assertTrue(self.reader.validate_session("session-1"))
        self.assertFalse(self.reader.validate_session("session"))
        self.assertFalse(self.reader.validate_session("../session-1"))
        self.write(event("session_meta", {"id": "other-session"}))
        self.assertFalse(self.reader.validate_session("session-1"))

    def test_plan_waits_for_matching_success_result(self):
        self.append(call("c1", "update_plan", {"plan": [{"step": "Build", "status": "in_progress"}]}))
        self.assertIsNone(self.reader.read("session-1")["plan"])
        self.append(output("wrong-id"))
        self.assertIsNone(self.reader.read("session-1")["plan"])
        self.append(output("c1"))
        result = self.reader.read("session-1")
        self.assertEqual(result["plan"]["nodes"][0]["status"], "in_progress")
        self.assertEqual(self.reader.read("session-1"), result)

    def test_failed_plan_call_does_not_change_progress(self):
        self.append(call("c1", "tools.update_plan", {"plan": [{"step": "Build", "status": "completed"}]}),
                    output("c1", json.dumps({"isError": True, "content": []})))
        result = self.reader.read("session-1")
        self.assertIsNone(result["plan"])
        self.assertEqual(result["runtime"]["status"], "recovering")

    def test_turn_end_does_not_remove_unfinished_plan(self):
        self.append(call("c1", "update_plan", {"plan": [{"step": "Build", "status": "in_progress"}]}),
                    output("c1"), event("event_msg", {"type": "task_complete"}))
        first = self.reader.read("session-1")
        self.assertEqual(first["runtime"]["status"], "turn_finished")
        self.assertEqual(first["plan"]["nodes"][0]["status"], "in_progress")
        self.append(event("event_msg", {"type": "task_started"}))
        self.assertEqual(self.reader.read("session-1")["plan"], first["plan"])

    def test_question_wait_resolves_only_for_matching_call(self):
        self.append(call("q1", "request_user_input", {"questions": []}))
        self.assertEqual(self.reader.read("session-1")["runtime"]["status"], "waiting_user")
        self.append(output("another", "answer"))
        self.assertEqual(self.reader.read("session-1")["runtime"]["status"], "waiting_user")
        self.append(output("q1", json.dumps({"answers": {}})))
        self.assertEqual(self.reader.read("session-1")["runtime"]["status"], "running")

    def test_unrelated_tool_completion_cannot_resolve_an_open_question(self):
        self.append(call("q1", "request_user_input", {"questions": []}),
                    event("event_msg", {"type": "item_completed", "thread_id": "session-1",
                                         "item": {"type": "CommandExecution", "exit_code": 0}}))
        self.assertEqual(self.reader.read("session-1")["runtime"]["status"], "waiting_user")
        self.append(event("event_msg", {"type": "turn_aborted"}),
                    event("event_msg", {"type": "task_started"}))
        self.assertEqual(self.reader.read("session-1")["runtime"]["status"], "running")

    def test_command_failure_does_not_fabricate_business_blocker(self):
        self.append(event("event_msg", {"type": "task_started"}),
                    event("event_msg", {"type": "item_completed", "thread_id": "session-1",
                                         "item": {"type": "CommandExecution", "exit_code": 1, "status": "Completed"}}))
        result = self.reader.read("session-1")
        self.assertEqual(result["runtime"]["status"], "recovering")
        self.assertIsNone(result["plan"])
        self.assertEqual(self.reader.read("session-1"), result)  # silence is not a blocker

    def test_observer_tools_do_not_wake_a_finished_turn_or_refresh_its_activity(self):
        self.append(event("event_msg", {"type": "task_complete"}))
        before = self.reader.read("session-1")
        tools = ["get_execution_panel_update", "list_execution_sessions",
                 "open_execution_panel", "show_execution_panel",
                 "submit_execution_feedback", "confirm_execution_feedback_delivery"]
        for prefix in ["", "mcp__codex_execution_panel__", "tools."]:
            for name in tools:
                with self.subTest(tool=prefix + name):
                    observation = event("event_msg", {
                        "type": "item_completed", "thread_id": "session-1",
                        "item": {"type": "McpToolCall", "server": "codex-execution-panel",
                                 "tool": prefix + name, "status": "Completed",
                                 "result": {"isError": False}}})
                    observation["timestamp"] = "2026-10-27T08:02:00Z"
                    self.append(observation)
                    self.assertEqual(self.reader.read("session-1"), before)

    def test_business_mcp_write_still_counts_as_work(self):
        self.append(event("event_msg", {"type": "task_complete"}),
                    event("event_msg", {"type": "item_completed", "thread_id": "session-1",
                                         "item": {"type": "McpToolCall", "tool": "set_execution_plan",
                                                  "status": "Completed", "result": {"isError": False}}}))
        self.assertEqual(self.reader.read("session-1")["runtime"]["status"], "running")

    def test_completed_mcp_call_with_error_result_is_recovering(self):
        self.append(event("event_msg", {"type": "item_completed", "thread_id": "session-1",
                                         "item": {"type": "McpToolCall", "tool": "update_execution_node",
                                                  "status": "Completed", "result": {"isError": True}}}))
        observed = self.reader.read("session-1")
        self.assertEqual(observed["runtime"]["status"], "recovering")
        self.assertIsNone(observed["plan"])

    def test_partial_record_only_applies_after_newline(self):
        self.append(call("c1", "update_plan", {"plan": [{"step": "Build", "status": "completed"}]}))
        raw = json.dumps(output("c1"))
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(raw[:20])
        self.assertIsNone(self.reader.read("session-1")["plan"])
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(raw[20:])
        self.assertIsNone(self.reader.read("session-1")["plan"])
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        self.assertIsNotNone(self.reader.read("session-1")["plan"])

    def test_truncation_discards_prior_projection(self):
        self.append(call("c1", "update_plan", {"plan": [{"step": "Build", "status": "completed"}]}), output("c1"))
        self.assertIsNotNone(self.reader.read("session-1")["plan"])
        self.write(self.meta)
        self.assertIsNone(self.reader.read("session-1")["plan"])

    def test_inherited_parent_plan_is_not_child_progress(self):
        meta = event("session_meta", {"id": "session-1", "parent_thread_id": "parent",
                                      "subagent_history_start_ordinal": 100}, 0)
        parent_call = call("p1", "update_plan", {"plan": [{"step": "Parent", "status": "completed"}]})
        parent_call["ordinal"] = 2
        parent_output = output("p1")
        parent_output["ordinal"] = 3
        self.write(meta, event("session_meta", {"id": "parent"}, 1), parent_call, parent_output,
                   event("event_msg", {"type": "task_started"}, 100),
                   event("event_msg", {"type": "item_completed", "thread_id": "parent",
                                        "item": {"exit_code": 1}}, 101))
        result = self.reader.read("session-1")
        self.assertIsNone(result["plan"])
        self.assertEqual(result["runtime"]["status"], "running")

    def test_raw_exec_is_never_evaluated_as_a_plan(self):
        self.append(event("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "x",
                                             "input": 'tools.update_plan({plan:[{step:"Fake",status:"completed"}]})'}), output("x"))
        self.assertIsNone(self.reader.read("session-1")["plan"])

    def test_plan_structure_change_is_visible_and_ids_survive_reordering(self):
        self.append(call("c1", "update_plan", {"plan": [{"step": "Build", "status": "pending"}, {"step": "Test", "status": "pending"}]}), output("c1"))
        before = self.reader.read("session-1")["plan"]["nodes"]
        self.append(call("c2", "update_plan", {"explanation": "Add compatibility check", "plan": [{"step": "Test", "status": "pending"}, {"step": "Build", "status": "in_progress"}, {"step": "Compatibility", "status": "pending"}]}), output("c2"))
        after = self.reader.read("session-1")
        self.assertEqual(before[0]["id"], after["plan"]["nodes"][1]["id"])
        self.assertEqual(after["planChanges"][0]["reason"], "Add compatibility check")
        self.assertEqual(len(after["planChanges"][0]["added"]), 1)


if __name__ == "__main__":
    unittest.main()
