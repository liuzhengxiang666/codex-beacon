"""Business-plan persistence and honest completion across concurrent clients."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from panel.state import PanelService


TEMP_ROOT = Path(tempfile.gettempdir()) / "codex-execution-panel"


class StateTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.root = Path(self.temp.name)
        self.codex_home = self.root / "codex"
        log_dir = self.codex_home / "sessions"
        log_dir.mkdir(parents=True)
        self.log = log_dir / "rollout-test-session-1.jsonl"
        self.log.write_text(json.dumps({"type": "session_meta", "payload": {"id": "session-1"}}) + "\n", encoding="utf-8")
        self.service = PanelService(self.root / "state", self.codex_home)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self):
        return self.service.set_plan("session-1", "Build a feature", [
            {"id": "inspect", "title": "Inspect", "status": "completed"},
            {"id": "build", "title": "Build", "status": "in_progress"},
            {"id": "test", "title": "Test"}])

    def test_missing_plan_has_no_invented_zero_percent(self):
        snapshot = self.service.snapshot("session-1")
        self.assertEqual(snapshot["source"], "none")
        self.assertIsNone(snapshot["progress"]["percent"])
        self.assertEqual(snapshot["nodes"], [])

    def test_dependency_fork_join_is_preserved_and_versioned(self):
        nodes = [{"id": "a", "title": "A", "dependsOn": []},
                 {"id": "b", "title": "B", "dependsOn": ["a"], "status": "completed"},
                 {"id": "c", "title": "C", "dependsOn": ["a"]},
                 {"id": "d", "title": "D", "dependsOn": ["b", "c"]}]
        self.service.set_plan("session-1", "Task", nodes)
        changed = [{**node} for node in nodes]
        changed[1]["dependsOn"] = []
        with self.assertRaises(ValueError):
            self.service.set_plan("session-1", "Task", changed)
        result = self.service.set_plan("session-1", "Task", changed, reason="independent work", expected_version=1)
        self.assertEqual(result["planVersion"], 2)
        self.assertEqual(result["nodes"][1]["status"], "pending")
        self.assertTrue(result["nodes"][1]["requiresRevalidation"])
        self.assertEqual(result["history"][0]["nodes"][1]["dependsOn"], ["a"])
        preserved = self.service.set_plan("session-1", "Task", [{"id": node["id"], "title": node["title"]} for node in nodes])
        self.assertEqual(preserved["nodes"][3]["dependsOn"], ["b", "c"])

    def test_invalid_dependencies_rollback_without_overwriting_a_plan(self):
        original = self.plan()
        for dependencies in (["missing"], ["inspect"], ["build", "build"], "build"):
            with self.subTest(dependencies=dependencies), self.assertRaises(ValueError):
                self.service.set_plan("session-1", "Task", [{"id": "inspect", "title": "Inspect", "dependsOn": dependencies}], reason="invalid")
        self.assertEqual(self.service.snapshot("session-1")["nodes"], original["nodes"])
        with self.assertRaisesRegex(ValueError, "循环"):
            self.service.set_plan("session-1", "Cycle", [{"id": "a", "title": "A", "dependsOn": ["b"]},
                                                       {"id": "b", "title": "B", "dependsOn": ["a"]}], reason="invalid")

    def test_exact_session_is_required_for_writes_and_reads(self):
        for identifier in ["session", "../../session-1", "missing", ""]:
            with self.assertRaises(ValueError):
                self.service.set_plan(identifier, "Task", [{"title": "Build"}])
            with self.assertRaises(ValueError):
                self.service.snapshot(identifier)

    def test_overall_progress_is_node_count_and_cancellation_is_explicit(self):
        first = self.plan()
        self.assertEqual(first["progress"], {"completed": 1, "total": 3, "cancelled": 0, "percent": 33})
        after = self.service.update_node("session-1", "test", status="cancelled")
        self.assertEqual(after["progress"], {"completed": 1, "total": 3, "cancelled": 1, "percent": 50})

    def test_all_cancelled_means_no_completion_measure(self):
        self.service.set_plan("session-1", "Task", [{"id": "one", "title": "One", "status": "cancelled"}])
        self.assertIsNone(self.service.snapshot("session-1")["progress"]["percent"])

    def test_completed_node_cannot_hide_open_blocker_or_decision(self):
        self.plan()
        self.service.update_node("session-1", "build", status="blocked", issue={
            "id": "api", "kind": "blocker", "title": "API unavailable", "detail": "Missing response"})
        before = self.service.snapshot("session-1")
        with self.assertRaises(ValueError):
            self.service.update_node("session-1", "build", status="completed")
        self.assertEqual(self.service.snapshot("session-1")["revision"], before["revision"])
        resolved = self.service.update_node("session-1", "build", status="completed", issue={
            "id": "api", "status": "resolved", "resolution": "API restored"})
        issue = resolved["nodes"][1]["issues"][0]
        self.assertIn("createdAt", issue)
        self.assertIn("resolvedAt", issue)
        self.assertEqual(issue["resolution"], "API restored")

    def test_issue_upserts_preserve_creation_and_resolution_times(self):
        self.plan()
        one = self.service.update_node("session-1", "build", issue={"id": "branch", "kind": "branch", "title": "Choose adapter", "options": ["A", "B"]})
        two = self.service.update_node("session-1", "build", issue={"id": "branch", "selected": "B", "status": "resolved", "resolution": "Use existing API"})
        three = self.service.update_node("session-1", "build", issue={"id": "branch", "detail": "Compatibility verified"})
        self.assertEqual(len(three["nodes"][1]["issues"]), 1)
        self.assertEqual(one["nodes"][1]["issues"][0]["createdAt"], three["nodes"][1]["issues"][0]["createdAt"])
        self.assertEqual(two["nodes"][1]["issues"][0]["resolvedAt"], three["nodes"][1]["issues"][0]["resolvedAt"])

    def test_pending_verification_is_not_revision_review_and_prevents_completion(self):
        self.plan()
        pending = self.service.update_node("session-1", "build", issue={
            "id": "acceptance", "kind": "verification", "title": "Native draft acceptance"})
        self.assertFalse(pending["nodes"][1]["needsReview"])
        self.assertFalse(pending["nodes"][1]["requiresRevalidation"])
        self.assertEqual(pending["progress"]["completed"], 1)
        with self.assertRaisesRegex(ValueError, "待验收"):
            self.service.update_node("session-1", "build", status="completed")
        nodes = [{**node} for node in pending["nodes"]]
        nodes[1]["status"] = "completed"
        with self.assertRaisesRegex(ValueError, "待验收"):
            self.service.set_plan("session-1", pending["title"], nodes)
        self.assertEqual(self.service.snapshot("session-1")["revision"], pending["revision"])
        done = self.service.update_node("session-1", "build", status="completed", issue={
            "id": "acceptance", "status": "resolved", "resolution": "Native draft verified"})
        self.assertEqual(done["nodes"][1]["status"], "completed")
        self.assertEqual(done["planVersion"], 1)

    def test_issue_reclassification_requires_reason_and_current_version_and_preserves_history(self):
        self.plan()
        self.service.update_node("session-1", "build", issue={
            "id": "acceptance", "kind": "blocker", "title": "Native acceptance", "detail": "Still to verify"})
        before = self.service.request_change("session-1", "test", "Pause after verification", kind="pause_after", request_id="pause-1")
        change = {"id": "acceptance", "kind": "verification"}
        for extra in ({}, {"reason": "Correct state meaning"}, {"expected_version": 1},
                      {"reason": "Correct state meaning", "expected_version": 9}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.service.update_node("session-1", "build", issue=change, **extra)
            self.assertEqual(self.service.snapshot("session-1")["revision"], before["revision"])
        result = self.service.update_node("session-1", "build", issue=change,
                                          reason="Correct state meaning", expected_version=1)
        self.assertEqual(result["planVersion"], 2)
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["nodes"][1]["issues"][0]["kind"], "blocker")
        old = before["nodes"][1]["issues"][0]
        current = result["nodes"][1]["issues"][0]
        self.assertEqual(current["kind"], "verification")
        for key in ("id", "createdAt", "title", "detail", "status"):
            self.assertEqual(current[key], old[key])
        self.assertEqual(result["nodes"][0], before["nodes"][0])
        self.assertFalse(result["nodes"][1]["needsReview"])
        self.assertEqual(result["requests"], before["requests"])
        self.assertEqual(result["pauseAfterNodeId"], "test")
        ordinary = self.service.update_node("session-1", "build", issue={
            "id": "acceptance", "kind": "verification", "detail": "Waiting for the same acceptance"})
        self.assertEqual(ordinary["planVersion"], 2)
        self.assertEqual(len(ordinary["history"]), 1)

    def test_acceptance_and_issue_reclassification_record_only_one_previous_version(self):
        self.plan()
        self.service.update_node("session-1", "build", issue={
            "id": "acceptance", "kind": "blocker", "title": "Acceptance"})
        changed = self.service.update_node("session-1", "build", acceptance="Check actual native draft",
                                           issue={"id": "acceptance", "kind": "verification"},
                                           reason="Clarify acceptance and state", expected_version=1)
        self.assertEqual(changed["planVersion"], 2)
        self.assertEqual(len(changed["history"]), 1)
        self.assertEqual(len(changed["planChanges"]), 1)
        previous = changed["history"][0]["nodes"][1]
        self.assertEqual(previous["acceptance"], "")
        self.assertEqual(previous["issues"][0]["kind"], "blocker")
        self.assertEqual(changed["nodes"][1]["acceptance"], "Check actual native draft")

    def test_set_plan_issue_reclassification_cannot_bypass_version_contract(self):
        self.plan()
        before = self.service.update_node("session-1", "build", issue={
            "id": "acceptance", "kind": "blocker", "title": "Acceptance"})
        nodes = [{**node} for node in before["nodes"]]
        nodes[1]["issues"] = [{"id": "acceptance", "kind": "verification"}]
        nodes[1]["acceptance"] = "Verify native behavior"
        for extra in ({"reason": "Reclassify"}, {"expected_version": 1}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.service.set_plan("session-1", before["title"], nodes, **extra)
        result = self.service.set_plan("session-1", before["title"], nodes,
                                       reason="Reclassify", expected_version=1)
        self.assertEqual(result["planVersion"], 2)
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["nodes"][1]["issues"][0]["kind"], "blocker")
        self.assertEqual(result["nodes"][0], before["nodes"][0])
        self.assertEqual(result["nodes"][1]["issues"][0]["createdAt"], before["nodes"][1]["issues"][0]["createdAt"])

    def test_reclassification_cannot_leave_completed_node_with_open_verification(self):
        self.plan()
        before = self.service.update_node("session-1", "inspect", issue={
            "id": "old-route", "kind": "branch", "title": "Previously considered route"})
        with self.assertRaisesRegex(ValueError, "待验收"):
            self.service.update_node("session-1", "inspect", issue={"id": "old-route", "kind": "verification"},
                                     reason="Requires acceptance", expected_version=1)
        self.assertEqual(self.service.snapshot("session-1")["revision"], before["revision"])
        changed = self.service.update_node("session-1", "inspect", status="in_progress",
                                           issue={"id": "old-route", "kind": "verification"},
                                           reason="Requires acceptance", expected_version=1)
        self.assertEqual(changed["planVersion"], 2)
        self.assertEqual(changed["nodes"][0]["status"], "in_progress")
        self.assertFalse(changed["nodes"][0]["requiresRevalidation"])

    def test_plan_change_requires_reason_and_preserves_baseline(self):
        self.plan()
        changed = [{"id": "inspect", "title": "Inspect", "status": "completed"}, {"id": "build", "title": "Build"}, {"id": "verify", "title": "Verify compatibility"}]
        with self.assertRaises(ValueError):
            self.service.set_plan("session-1", "Build a feature", changed)
        result = self.service.set_plan("session-1", "Build a feature", changed, reason="Compatibility check replaces generic tests")
        self.assertEqual(result["planChanges"][0]["added"], ["verify"])
        self.assertEqual(result["planChanges"][0]["removed"], ["test"])
        with closing(sqlite3.connect(self.service.db_path)) as connection:
            document = json.loads(connection.execute("SELECT document FROM plans").fetchone()[0])
        self.assertEqual([node["id"] for node in document["baseline"]], ["inspect", "build", "test"])

    def test_completed_business_plan_survives_turn_boundary_and_restart(self):
        self.plan()
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
        fresh = PanelService(self.root / "state", self.codex_home)
        snapshot = fresh.snapshot("session-1")
        self.assertEqual(len(snapshot["nodes"]), 3)
        self.assertEqual(snapshot["runtime"]["status"], "turn_finished")

    def test_concurrent_services_do_not_overwrite_other_node_updates(self):
        self.plan()
        other = PanelService(self.root / "state", self.codex_home)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.service.update_node, "session-1", "build", summary="Implemented")
            second = pool.submit(other.update_node, "session-1", "test", summary="Test selection ready")
            first.result()
            second.result()
        nodes = self.service.snapshot("session-1")["nodes"]
        self.assertEqual(nodes[1]["summary"], "Implemented")
        self.assertEqual(nodes[2]["summary"], "Test selection ready")

    def test_repeated_reads_have_stable_revision(self):
        first = self.plan()
        self.assertEqual(first["revision"], self.service.snapshot("session-1")["revision"])
        changed = self.service.update_node("session-1", "build", summary="Working on API")
        self.assertNotEqual(first["revision"], changed["revision"])

    def test_structure_versions_capture_full_prior_nodes_and_archive_removed_work(self):
        self.plan()
        self.service.update_node("session-1", "build", status="blocked", issue={
            "id": "api", "kind": "blocker", "title": "Missing API", "detail": "No required field"},
            evidence=[{"label": "Contract", "url": "https://example.test/contract", "detail": "Required field absent"}])
        changed = self.service.set_plan("session-1", "Build a feature", [
            {"id": "inspect", "title": "Inspect"}, {"id": "test", "title": "Test"}],
            reason="Replace the blocked integration", expected_version=1)
        self.assertEqual(changed["planVersion"], 2)
        historical = changed["history"][0]
        self.assertEqual(historical["version"], 1)
        self.assertEqual(historical["nodes"][1]["issues"][0]["detail"], "No required field")
        self.assertEqual(historical["nodes"][1]["evidence"][0]["label"], "Contract")
        archived = changed["archivedNodes"][0]
        self.assertEqual(archived["id"], "build")
        self.assertEqual(archived["status"], "blocked")
        self.assertEqual(archived["issues"][0]["id"], "api")
        self.assertEqual(archived["archivedVersion"], 1)
        self.assertEqual(archived["archiveReason"], "Replace the blocked integration")
        restored = self.service.set_plan("session-1", "Build a feature", [
            {"id": "inspect", "title": "Inspect"}, {"id": "build", "title": "Build"}],
            reason="Restore integration with its evidence", expected_version=2)
        self.assertEqual(restored["nodes"][1]["issues"][0]["title"], "Missing API")
        self.assertEqual(restored["nodes"][1]["evidence"][0]["detail"], "Required field absent")
        self.assertEqual([entry["version"] for entry in restored["history"]], [1, 2])

    def test_completed_renamed_node_reopens_even_when_completed_is_explicit(self):
        self.plan()
        changed = self.service.set_plan("session-1", "Build a feature", [
            {"id": "inspect", "title": "Inspect the new contract", "status": "completed"},
            {"id": "build", "title": "Build"}, {"id": "test", "title": "Test"}],
            reason="The expected contract changed")
        self.assertEqual(changed["nodes"][0]["status"], "pending")
        self.assertTrue(changed["nodes"][0]["needsReview"])
        self.assertEqual(changed["progress"]["completed"], 0)

    def test_acceptance_change_creates_version_and_requires_revalidation(self):
        self.plan()
        original = self.service.snapshot("session-1")
        with self.assertRaises(ValueError):
            self.service.update_node("session-1", "inspect", acceptance="Check both APIs")
        self.assertEqual(self.service.snapshot("session-1")["revision"], original["revision"])
        changed = self.service.update_node("session-1", "inspect", acceptance="Check both APIs", reason="Second API added", expected_version=1)
        self.assertEqual(changed["planVersion"], 2)
        self.assertEqual(changed["nodes"][0]["status"], "pending")
        self.assertEqual(changed["history"][0]["nodes"][0]["acceptance"], "")
        self.assertTrue(changed["nodes"][0]["needsReview"])
        verified = self.service.update_node("session-1", "inspect", status="completed", evidence=[{"label": "API checks", "detail": "Both passed"}])
        self.assertEqual(verified["planVersion"], 2)
        self.assertFalse(verified["nodes"][0]["needsReview"])

    def test_expected_version_rejects_stale_writes_transactionally(self):
        self.plan()
        self.service.update_node("session-1", "build", acceptance="New requirement", reason="Clarified scope")
        before = self.service.snapshot("session-1")
        for operation in [
            lambda: self.service.set_plan("session-1", "Wrong old title", before["nodes"], reason="Stale title", expected_version=1),
            lambda: self.service.update_node("session-1", "build", summary="Stale summary", expected_version=1),
            lambda: self.service.update_node("session-1", "build", summary="Boolean version", expected_version=True),
        ]:
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.service.snapshot("session-1")["revision"], before["revision"])

    def test_evidence_validation_rejects_executable_links_and_preserves_original(self):
        self.plan()
        before = self.service.snapshot("session-1")
        for value in [[{"label": "Dangerous", "url": "javascript:alert(1)"}], [{"label": "Unknown", "command": "run"}], [{"label": ""}]]:
            with self.assertRaises(ValueError):
                self.service.update_node("session-1", "build", evidence=value)
        self.assertEqual(self.service.snapshot("session-1")["revision"], before["revision"])

    def test_modify_request_preserves_approved_plan_and_marks_review(self):
        self.plan()
        value = self.service.request_change("session-1", "inspect", "Also inspect compatibility", request_id="change-1")
        self.assertEqual(value["planVersion"], 1)
        self.assertEqual(value["nodes"][0]["title"], "Inspect")
        self.assertEqual(value["nodes"][0]["status"], "completed")
        self.assertTrue(value["nodes"][0]["needsReview"])
        self.assertEqual(value["requests"][0]["status"], "pending")
        repeated = self.service.request_change("session-1", "inspect", "Also inspect compatibility", request_id="change-1")
        self.assertEqual(repeated["revision"], value["revision"])
        with self.assertRaises(ValueError):
            self.service.request_change("session-1", "inspect", "Different intent", request_id="change-1")
        dismissed = self.service.resolve_request("session-1", "change-1", "dismissed", "Already covered by the existing contract")
        self.assertFalse(dismissed["nodes"][0]["needsReview"])
        self.assertEqual(dismissed["planVersion"], 1)

    def test_applying_plan_change_retains_review_until_validation_and_resolution(self):
        self.plan()
        self.service.request_change("session-1", "inspect", "Check the second API", request_id="change-2")
        changed = self.service.set_plan("session-1", "Build a feature", [
            {"id": "inspect", "title": "Inspect", "acceptance": "Both APIs must be compatible"},
            {"id": "build", "title": "Build"}, {"id": "test", "title": "Test"}], reason="User requested the second API")
        self.assertEqual(changed["nodes"][0]["status"], "pending")
        resolved = self.service.resolve_request("session-1", "change-2", "applied", "Scope and acceptance updated in version 2; integration needs revalidation")
        self.assertTrue(resolved["nodes"][0]["needsReview"])
        self.assertEqual(resolved["requests"][0]["resolvedPlanVersion"], 2)
        done = self.service.update_node("session-1", "inspect", status="completed", evidence=[{"label": "Compatibility", "detail": "Both API checks pass"}])
        self.assertFalse(done["nodes"][0]["needsReview"])
        self.assertEqual(len(done["history"]), 1)

    def test_pause_requests_do_not_fabricate_paused_runtime_and_survive_restart(self):
        self.plan()
        requested = self.service.request_change("session-1", "build", "Pause after this checkpoint", kind="pause_after", request_id="pause-1")
        self.assertEqual(requested["pauseAfterNodeId"], "build")
        self.assertNotEqual(requested["runtime"]["status"], "paused")
        newer = self.service.request_change("session-1", "test", "Pause after validation instead", kind="pause_after", request_id="pause-2")
        self.assertEqual(newer["pauseAfterNodeId"], "test")
        self.assertEqual(newer["requests"][0]["status"], "dismissed")
        restarted = PanelService(self.root / "state", self.codex_home)
        self.assertEqual(restarted.snapshot("session-1")["pauseAfterNodeId"], "test")
        resolved = restarted.resolve_request("session-1", "pause-2", "applied", "Native host confirmed pause")
        self.assertIsNone(resolved["pauseAfterNodeId"])
        self.assertNotEqual(resolved["runtime"]["status"], "paused")

    def test_global_pause_request_does_not_need_a_plan(self):
        snapshot = self.service.request_change("session-1", None, "Please pause now", kind="pause_now", request_id="pause-now")
        self.assertEqual(snapshot["planVersion"], 0)
        self.assertEqual(snapshot["nodes"], [])
        self.assertIsNone(snapshot["progress"]["percent"])
        plan = self.plan()
        self.assertEqual(plan["planVersion"], 1)
        self.assertEqual(plan["requests"][0]["id"], "pause-now")

    def test_legacy_document_fields_migrate_without_losing_user_state(self):
        self.plan()
        with closing(sqlite3.connect(self.service.db_path)) as connection:
            document = json.loads(connection.execute("SELECT document FROM plans").fetchone()[0])
            for key in ["planVersion", "history", "archivedNodes", "requests", "pauseAfterNodeId"]:
                document.pop(key, None)
            for node in document["nodes"]:
                for key in ["acceptance", "evidence", "needsReview", "requiresRevalidation"]:
                    node.pop(key, None)
            connection.execute("UPDATE plans SET document=?", (json.dumps(document),))
            connection.commit()
        migrated = self.service.snapshot("session-1")
        self.assertEqual(migrated["planVersion"], 1)
        self.assertEqual(migrated["nodes"][1]["status"], "in_progress")
        self.assertEqual(migrated["nodes"][0]["evidence"], [])
        changed = self.service.set_plan("session-1", "Renamed task", migrated["nodes"], reason="Clarified title")
        self.assertEqual(changed["history"][0]["title"], "Build a feature")

    def test_concurrent_plan_edits_with_same_expected_version_only_apply_once(self):
        original = self.plan()
        other = PanelService(self.root / "state", self.codex_home)
        def edit(service, title):
            try:
                service.set_plan("session-1", title, original["nodes"], reason="Explicit title edit", expected_version=1)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(edit, self.service, "First edit")
            second = pool.submit(edit, other, "Second edit")
            applied = [first.result(), second.result()]
        self.assertEqual(sum(applied), 1)
        self.assertEqual(self.service.snapshot("session-1")["planVersion"], 2)


if __name__ == "__main__":
    unittest.main()
