"""Feedback is durable user intent; delivery receipts never fabricate execution control."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from panel.interactions import Interactions
from panel.protocol import PanelAPI
from panel.state import PanelService


TEMP_ROOT = Path(tempfile.gettempdir()) / "codex-execution-panel"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION = "feedback-test-1"
OTHER_SESSION = "feedback-test-2"


class InteractionTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.root = Path(self.temporary.name)
        self.codex_home = self.root / "codex"
        log_dir = self.codex_home / "sessions"
        log_dir.mkdir(parents=True)
        for session_id in (SESSION, OTHER_SESSION):
            lines = [
                {"type": "session_meta", "payload": {"id": session_id}},
                {"type": "event_msg", "timestamp": "2026-10-27T08:00:00Z", "payload": {"type": "task_started"}},
            ]
            (log_dir / f"rollout-test-{session_id}.jsonl").write_text(
                "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        self.service = PanelService(self.root / "state", self.codex_home)
        self.service.set_plan(SESSION, "Test feature", [
            {"id": "build", "title": "Build the feature", "status": "in_progress"},
            {"id": "verify", "title": "Verify the result"},
        ])
        self.interactions = Interactions(self.service)
        self.api = PanelAPI(self.service, PROJECT_ROOT)

    def tearDown(self):
        self.temporary.cleanup()

    def submit(self, request_id="feedback-1", *, allow_handoff=True, kind="modify", node_id="build", message="Use the existing API"):
        return self.interactions.submit(SESSION, node_id, message, kind, request_id, allow_handoff)

    def api_submit(self, request_id="feedback-api", meta=None, allow_handoff=True):
        metadata = {"thread_id": SESSION} if meta is None else meta
        return self.api.call("submit_execution_feedback", {
            "sessionId": SESSION, "nodeId": "build", "message": "Keep the current API", "kind": "modify", "requestId": request_id,
        }, metadata, allow_handoff=allow_handoff)["structuredContent"]

    def delivery_rows(self):
        with closing(sqlite3.connect(self.service.db_path)) as db:
            return db.execute("SELECT request_id,status,message,updated_at FROM feedback_delivery ORDER BY request_id").fetchall()

    def test_explicit_submit_offers_handoff_once_and_duplicate_id_does_not_send_again(self):
        first = self.submit()
        self.assertEqual(first["delivery"]["status"], "offered")
        self.assertEqual(first["handoff"]["requestId"], "feedback-1")
        self.assertIn("Build the feature", first["handoff"]["prompt"])
        self.assertIn(SESSION, first["handoff"]["prompt"])
        self.assertIn("核对", first["handoff"]["prompt"])
        self.assertIn("不创建新 Goal", first["handoff"]["prompt"])
        duplicate = self.submit()
        self.assertNotIn("handoff", duplicate)
        self.assertEqual(len(duplicate["requests"]), 1)
        self.assertEqual(len(self.delivery_rows()), 1)

    def test_two_open_views_cannot_offer_the_same_handoff_twice(self):
        second_service = PanelService(self.root / "state", self.codex_home)
        second = Interactions(second_service)
        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(self.submit, "concurrent")
            right = pool.submit(second.submit, SESSION, "build", "Use the existing API", "modify", "concurrent", True)
            results = [left.result(), right.result()]
        self.assertEqual(sum("handoff" in value for value in results), 1)
        self.assertEqual(len(self.service.snapshot(SESSION)["requests"]), 1)

    def test_preview_submission_only_records_pending_and_has_no_message_handoff(self):
        value = self.api_submit(allow_handoff=False)
        self.assertNotIn("handoff", value)
        self.assertEqual(value["delivery"]["status"], "pending")
        self.assertEqual(value["requests"][0]["status"], "pending")
        self.assertEqual(value["requests"][0]["delivery"]["status"], "pending")
        self.assertEqual(value["runtime"]["status"], "running")
        repeated = self.api_submit(allow_handoff=False)
        self.assertNotIn("handoff", repeated)
        self.assertEqual(len(repeated["requests"]), 1)

    def test_missing_host_identity_only_records_pending_and_explains_handoff_block(self):
        for index, metadata in enumerate(({}, {"openai/session": OTHER_SESSION})):
            with self.subTest(metadata=metadata):
                value = self.api_submit(request_id=f"unknown-host-{index}", meta=metadata)
                self.assertNotIn("handoff", value)
                self.assertEqual(value["delivery"]["status"], "pending")
                self.assertRegex(value["delivery"]["message"], "身份|上下文")
                self.assertNotIn("预览", value["delivery"]["message"])
                self.assertEqual(value["requests"][-1]["status"], "pending")
                self.assertNotEqual(value["runtime"]["status"], "paused")

    def test_explicit_verified_resubmit_offers_a_previously_unsent_request_once(self):
        pending = self.api_submit(request_id="later-verified", meta={})
        self.assertEqual(pending["delivery"]["status"], "pending")
        self.assertNotIn("handoff", pending)
        verified = self.api_submit(request_id="later-verified", meta={"thread_id": SESSION})
        self.assertEqual(verified["delivery"]["status"], "offered")
        self.assertIn(SESSION, verified["handoff"]["prompt"])
        self.assertEqual(verified["handoff"]["requestId"], "later-verified")
        repeated = self.api_submit(request_id="later-verified", meta={"threadId": SESSION})
        self.assertNotIn("handoff", repeated)
        self.assertEqual(len(repeated["requests"]), 1)

    def test_matching_host_aliases_allow_one_same_chat_handoff(self):
        value = self.api_submit(meta={"thread_id": SESSION, "threadId": SESSION})
        self.assertIn("handoff", value)
        self.assertIn(SESSION, value["handoff"]["prompt"])

    def test_sent_receipt_cannot_be_downgraded_and_never_means_applied(self):
        self.submit()
        sent = self.interactions.confirm_delivery(SESSION, "feedback-1", "sent")
        self.assertEqual(sent["requests"][0]["delivery"]["status"], "sent")
        self.assertEqual(sent["requests"][0]["status"], "pending")
        rows = self.delivery_rows()
        self.interactions.confirm_delivery(SESSION, "feedback-1", "failed")
        self.interactions.confirm_delivery(SESSION, "feedback-1", "unconfirmed")
        self.assertEqual(self.delivery_rows(), rows)
        self.assertNotIn("handoff", self.submit())

    def test_definite_delivery_failure_can_be_explicitly_retried_once(self):
        self.submit()
        self.interactions.confirm_delivery(SESSION, "feedback-1", "failed")
        retry = self.submit()
        self.assertIn("handoff", retry)
        self.assertEqual(retry["delivery"]["status"], "offered")
        self.assertEqual(len(retry["requests"]), 1)
        self.assertNotIn("handoff", self.submit())

    def test_ambiguous_delivery_is_not_automatically_resent(self):
        self.submit()
        value = self.interactions.confirm_delivery(SESSION, "feedback-1", "unconfirmed")
        self.assertEqual(value["requests"][0]["delivery"]["status"], "unconfirmed")
        self.assertNotIn("handoff", self.submit())
        self.assertNotIn("handoff", self.api.snapshot(SESSION))
        self.assertNotEqual(value["runtime"]["status"], "paused")

    def test_confirmation_requires_an_existing_delivery_and_valid_status(self):
        self.service.request_change(SESSION, "build", "An unsent draft", request_id="draft-only")
        with self.assertRaises(ValueError):
            self.interactions.confirm_delivery(SESSION, "draft-only", "sent")
        with self.assertRaises(ValueError):
            self.interactions.confirm_delivery(SESSION, "missing", "sent")
        self.submit()
        with self.assertRaises(ValueError):
            self.interactions.confirm_delivery(SESSION, "feedback-1", "paused")

    def test_polling_and_reading_never_offer_handoff_or_change_delivery(self):
        self.submit(kind="pause_now", node_id=None, message="Pause before making more changes")
        rows = self.delivery_rows()
        for _ in range(3):
            snapshot = self.api.snapshot(SESSION)
            read = self.api.call("read_execution_plan", {"sessionId": SESSION})["structuredContent"]
            poll = self.api.call("get_execution_panel_update", {"sessionId": SESSION, "revision": snapshot["revision"]})["structuredContent"]
            for value in (snapshot, read, poll):
                self.assertNotIn("handoff", value)
                self.assertNotEqual(value.get("runtime", {}).get("status"), "paused")
            self.assertTrue(poll["unchanged"])
        self.assertEqual(self.delivery_rows(), rows)
        self.assertEqual(snapshot["host"]["control"]["status"], "requested")
        self.assertFalse(snapshot["host"]["available"])

    def test_checkpoint_directive_only_appears_after_the_node_completes(self):
        requested = self.submit(kind="pause_after", message="Stop before starting the verification")
        self.assertFalse(requested["executionDirective"]["pauseRequested"])
        self.assertFalse(requested["executionDirective"]["checkpointReached"])
        self.service.update_node(SESSION, "build", status="completed")
        reached = self.api.snapshot(SESSION)
        self.assertTrue(reached["executionDirective"]["checkpointReached"])
        self.assertEqual(reached["executionDirective"]["requestIds"], ["feedback-1"])
        self.assertEqual(reached["runtime"]["status"], "running")
        self.assertEqual(reached["nodes"][1]["status"], "pending")
        self.assertNotIn("handoff", reached)
        self.service.resolve_request(SESSION, "feedback-1", "applied", "User checkpoint handled through the native mechanism")
        resolved = self.api.snapshot(SESSION)
        self.assertFalse(resolved["executionDirective"]["checkpointReached"])

    def test_end_of_turn_is_idle_and_does_not_claim_native_pause(self):
        self.submit(kind="pause_now", node_id=None, message="Pause when safe")
        log = self.codex_home / "sessions" / f"rollout-test-{SESSION}.jsonl"
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "event_msg", "timestamp": "2026-10-27T08:01:00Z", "payload": {"type": "task_complete"}}) + "\n")
        value = self.api.snapshot(SESSION)
        self.assertEqual(value["runtime"]["status"], "turn_finished")
        self.assertEqual(value["host"]["control"]["status"], "idle")
        self.assertFalse(value["host"]["available"])
        self.assertNotIn("paused", json.dumps(value["host"]))

    def test_feedback_and_delivery_confirmation_reject_conflicting_host_identity(self):
        for metadata in [{"thread_id": OTHER_SESSION}, {"thread_id": SESSION, "threadId": OTHER_SESSION}]:
            with self.assertRaises(ValueError):
                self.api_submit(meta=metadata)
        self.assertEqual(self.service.snapshot(SESSION)["requests"], [])
        self.assertEqual(self.delivery_rows(), [])
        self.api_submit()
        with self.assertRaises(ValueError):
            self.api.call("confirm_execution_feedback_delivery", {
                "sessionId": SESSION, "requestId": "feedback-api", "status": "sent",
            }, {"thread_id": OTHER_SESSION})
        self.assertEqual(self.delivery_rows()[0][1], "offered")

    def test_receipts_and_plan_history_change_revision_but_reads_are_stable(self):
        initial = self.api.snapshot(SESSION)
        offered = self.api_submit()
        self.assertNotEqual(offered["revision"], initial["revision"])
        confirmed = self.api.call("confirm_execution_feedback_delivery", {
            "sessionId": SESSION, "requestId": "feedback-api", "status": "sent",
        })["structuredContent"]
        self.assertNotEqual(confirmed["revision"], offered["revision"])
        self.assertEqual(confirmed["revision"], self.api.snapshot(SESSION)["revision"])
        self.service.set_plan(SESSION, "Test feature", [
            {"id": "build", "title": "Build the feature", "acceptance": "Keep the existing API"},
            {"id": "verify", "title": "Verify the result"},
        ], reason="Clarify the accepted feedback", expected_version=1)
        versioned = self.api.snapshot(SESSION)
        self.assertNotEqual(versioned["revision"], confirmed["revision"])
        self.assertEqual(versioned["planVersion"], 2)
        self.assertEqual(versioned["history"][0]["version"], 1)
        self.assertEqual(versioned["requests"][0]["delivery"]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
