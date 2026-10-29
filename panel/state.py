"""Transactional, session-bound business progress for the execution panel."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterator
from urllib.parse import urlsplit
import uuid

from .logs import CodexLogReader, PLAN_STATUSES, valid_session_id


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value: Any, name: str, limit: int = 500, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} 必须是{'非空' if required else ''}文本，且不超过 {limit} 字符")
    return value.strip()


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} 格式不正确")
    return value


def _expected_version(value: Any, actual: int) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError("expected_version 必须是非负整数")
    if value is not None and value != actual:
        raise ValueError(f"计划版本已变化：期望 {value}，当前为 {actual}，请重新读取后再修改")


def _evidence(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or len(raw) > 30:
        raise ValueError("evidence 必须是最多 30 项的列表")
    result = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"label", "url", "detail"}:
            raise ValueError("每条 evidence 只允许 label、url 和 detail 字段")
        value = {"label": _text(item.get("label"), "evidence.label", 200, True)}
        if "url" in item:
            url = _text(item["url"], "evidence.url", 2000)
            if url and (urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc):
                raise ValueError("evidence.url 必须是完整的 http 或 https 地址；本地路径请写入 detail")
            value["url"] = url
        if "detail" in item:
            value["detail"] = _text(item["detail"], "evidence.detail", 2000)
        result.append(value)
    return result


def _upgrade_document(document: dict[str, Any]) -> dict[str, Any]:
    """Add compatible fields in memory; never rewrite logs or infer old versions."""
    document.setdefault("planVersion", 1 if document.get("nodes") else 0)
    document.setdefault("history", [])
    document.setdefault("archivedNodes", [])
    document.setdefault("requests", [])
    document.setdefault("pauseAfterNodeId", None)
    document.setdefault("planChanges", [])
    document.setdefault("baseline", copy.deepcopy(document.get("nodes", [])))
    for node in document.get("nodes", []):
        node.setdefault("acceptance", "")
        node.setdefault("evidence", [])
        node.setdefault("requiresRevalidation", False)
        node.setdefault("needsReview", False)
    return document


def _refresh_review(document: dict[str, Any]) -> None:
    pending = {item["nodeId"] for item in document["requests"]
               if item["kind"] == "modify" and item["status"] == "pending"}
    for node in document["nodes"]:
        node["needsReview"] = bool(node.get("requiresRevalidation") or node["id"] in pending)
    checkpoints = [item for item in document["requests"]
                   if item["kind"] == "pause_after" and item["status"] == "pending"]
    document["pauseAfterNodeId"] = checkpoints[-1]["nodeId"] if checkpoints else None


def _record_version(document: dict[str, Any], at: str, reason: str) -> None:
    document["history"].append({
        "version": document["planVersion"], "at": document.get("updatedAt") or at,
        "reason": reason, "title": document["title"], "nodes": copy.deepcopy(document["nodes"]),
        "requests": copy.deepcopy(document["requests"]),
        "pauseAfterNodeId": document.get("pauseAfterNodeId"),
    })
    document["planVersion"] += 1


def _node_is_completable(node: dict[str, Any]) -> None:
    if node["status"] == "completed" and any(
        issue["status"] == "open" and issue["kind"] in {"blocker", "decision", "verification"}
        for issue in node["issues"]
    ):
        raise ValueError("请先解决该节点的卡点、待决定或待验收事项，再标为已完成")


def _validate_dependencies(nodes: list[dict[str, Any]]) -> None:
    """Explicit dependencies form a DAG; omitted fields retain the legacy sequence."""
    by_id = {node["id"]: node for node in nodes}
    edges = {}
    for index, node in enumerate(nodes):
        values = node.get("dependsOn", [nodes[index - 1]["id"]] if index else [])
        if not isinstance(values, list) or len(values) > 20:
            raise ValueError("node.dependsOn 必须是最多 20 项的节点 ID 列表")
        values = [_identifier(value, "dependsOn") for value in values]
        if len(values) != len(set(values)) or node["id"] in values:
            raise ValueError("依赖不能重复或指向节点自身")
        if any(value not in by_id for value in values):
            raise ValueError("依赖节点不存在；删除节点时请同时明确调整依赖")
        edges[node["id"]] = values
    visiting, visited = set(), set()

    def visit(identifier):
        if identifier in visiting:
            raise ValueError("执行依赖不能形成循环")
        if identifier in visited:
            return
        visiting.add(identifier)
        for parent in edges[identifier]:
            visit(parent)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in edges:
        visit(identifier)


def _normalize_issue(raw: Any, existing: dict[str, Any] | None, at: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("issue 必须是对象")
    issue = copy.deepcopy(existing or {})
    issue_id = _identifier(raw.get("id"), "issue.id")
    kind = raw.get("kind", issue.get("kind"))
    status_value = raw.get("status", issue.get("status", "open"))
    if kind not in {"blocker", "branch", "decision", "verification"}:
        raise ValueError("issue.kind 必须是 blocker、branch、decision 或 verification")
    if status_value not in {"open", "resolved"}:
        raise ValueError("issue.status 必须是 open 或 resolved")
    issue.update({"id": issue_id, "kind": kind, "status": status_value,
                  "title": _text(raw.get("title", issue.get("title")), "issue.title", 200, True),
                  "detail": _text(raw.get("detail", issue.get("detail", "")), "issue.detail", 2000),
                  "createdAt": issue.get("createdAt", at), "updatedAt": at})
    if "options" in raw:
        options = raw["options"]
        if not isinstance(options, list) or len(options) > 8:
            raise ValueError("issue.options 必须是最多 8 项的列表")
        issue["options"] = [_text(option, "option", 200, True) for option in options]
    if "selected" in raw:
        selected = _text(raw["selected"], "issue.selected", 200)
        if selected and issue.get("options") and selected not in issue["options"]:
            raise ValueError("selected 必须对应一个分支选项")
        issue["selected"] = selected
    if "resolution" in raw:
        issue["resolution"] = _text(raw["resolution"], "issue.resolution", 2000)
    if status_value == "resolved" and (not existing or existing.get("status") != "resolved"):
        issue["resolvedAt"] = at
    return issue


class PanelService:
    def __init__(self, state_dir: Path, codex_home: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "execution-panel.sqlite3"
        self.logs = CodexLogReader(Path(codex_home))
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS plans (session_id TEXT PRIMARY KEY, document TEXT NOT NULL)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def validate_session(self, session_id: str) -> bool:
        return valid_session_id(session_id) and self.logs.validate_session(session_id)

    def _require_session(self, session_id: str) -> None:
        if not self.validate_session(session_id):
            raise ValueError("必须提供与真实本地 Codex 会话元信息精确匹配的 sessionId")

    @staticmethod
    def _load(connection: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT document FROM plans WHERE session_id=?", (session_id,)).fetchone()
        return _upgrade_document(json.loads(row[0])) if row else None

    @staticmethod
    def _save(connection: sqlite3.Connection, session_id: str, document: dict[str, Any]) -> None:
        data = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        connection.execute("INSERT INTO plans(session_id, document) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET document=excluded.document", (session_id, data))

    def set_plan(self, session_id: str, title: str, nodes: list[dict[str, Any]], reason: str | None = None,
                 expected_version: int | None = None) -> dict[str, Any]:
        self._require_session(session_id)
        title = _text(title, "title", 200, True)
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
            raise ValueError("nodes 必须包含 1 到 100 个主要节点")
        reason_text = _text(reason, "reason", 2000)
        at = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._load(connection, session_id)
            _expected_version(expected_version, previous["planVersion"] if previous else 0)
            previous_nodes = {node["id"]: node for node in previous["nodes"]} if previous else {}
            title_ids = {node["title"]: node["id"] for node in previous_nodes.values()}
            archived_by_id = {node["id"]: node for node in previous["archivedNodes"]} if previous else {}
            normalized = []
            used = set()
            issue_kind_changed = False
            for raw in nodes:
                if not isinstance(raw, dict):
                    raise ValueError("每个 node 必须是对象")
                node_title = _text(raw.get("title", raw.get("step")), "node.title", 200, True)
                identifier = raw.get("id") or title_ids.get(node_title) or "n_" + hashlib.sha256(node_title.encode("utf-8")).hexdigest()[:12]
                identifier = _identifier(identifier, "node.id")
                if identifier in used:
                    raise ValueError("节点 ID 必须唯一；同名节点请明确指定不同 ID")
                used.add(identifier)
                old = previous_nodes.get(identifier) or archived_by_id.get(identifier)
                status_value = raw.get("status", old["status"] if old else "pending")
                if status_value not in PLAN_STATUSES:
                    raise ValueError("node.status 无效")
                node = {"id": identifier, "title": node_title, "status": status_value,
                        "summary": _text(raw.get("summary", old["summary"] if old else ""), "node.summary", 1000),
                        "issues": copy.deepcopy(old["issues"] if old else []),
                        "acceptance": _text(raw.get("acceptance", old.get("acceptance", "") if old else ""), "node.acceptance", 4000),
                        "evidence": _evidence(raw["evidence"]) if "evidence" in raw else copy.deepcopy(old.get("evidence", []) if old else []),
                        "requiresRevalidation": bool(old.get("requiresRevalidation")) if old else False,
                        "needsReview": bool(old.get("needsReview")) if old else False}
                if "dependsOn" in raw or old and "dependsOn" in old:
                    node["dependsOn"] = copy.deepcopy(raw.get("dependsOn", old.get("dependsOn") if old else []))
                semantic_change = bool(old and (old["title"] != node_title or old.get("acceptance", "") != node["acceptance"]
                                               or "dependsOn" in old and old["dependsOn"] != node.get("dependsOn")))
                if old and old["status"] == "completed" and semantic_change:
                    if node["status"] == "completed":
                        node["status"] = "pending"
                    node["requiresRevalidation"] = True
                elif node["status"] == "completed":
                    node["requiresRevalidation"] = False
                if "issues" in raw:
                    if not isinstance(raw["issues"], list) or len(raw["issues"]) > 100:
                        raise ValueError("node.issues 必须是最多 100 项的列表")
                    by_id = {issue["id"]: issue for issue in node["issues"]}
                    prior_kinds = {identifier: issue["kind"] for identifier, issue in by_id.items()}
                    for issue_raw in raw["issues"]:
                        issue_id = issue_raw.get("id") if isinstance(issue_raw, dict) else None
                        existing_issue = by_id.get(issue_id)
                        issue = _normalize_issue(issue_raw, existing_issue, at)
                        by_id[issue["id"]] = issue
                    issue_kind_changed |= any(by_id[identifier]["kind"] != kind for identifier, kind in prior_kinds.items())
                    node["issues"] = list(by_id.values())
                _node_is_completable(node)
                normalized.append(node)
            _validate_dependencies(normalized)
            if issue_kind_changed and expected_version is None:
                raise ValueError("修改已有事项类型必须提供 expectedVersion")
            structure = [(node["id"], node["title"], node["acceptance"], node.get("dependsOn")) for node in normalized]
            old_structure = [(node["id"], node["title"], node.get("acceptance", ""), node.get("dependsOn")) for node in previous["nodes"]] if previous else []
            changes = copy.deepcopy(previous.get("planChanges", [])) if previous else []
            changed = bool(previous and previous["nodes"] and (structure != old_structure or title != previous["title"] or issue_kind_changed))
            document = copy.deepcopy(previous) if previous else _upgrade_document({"title": title, "nodes": [], "updatedAt": at})
            if changed:
                if not reason_text:
                    raise ValueError("修改计划的节点、顺序、标题、验收标准或事项类型时必须说明 reason")
                _record_version(document, at, reason_text)
                changes.append({"at": at, "reason": reason_text,
                                "added": [node["id"] for node in normalized if node["id"] not in previous_nodes],
                                "removed": [node_id for node_id in previous_nodes if node_id not in used]})
                for old_id, old_node in previous_nodes.items():
                    if old_id not in used:
                        archived = copy.deepcopy(old_node)
                        archived.update({"archivedAt": at, "archivedVersion": previous["planVersion"], "archiveReason": reason_text})
                        document["archivedNodes"].append(archived)
            elif not previous or not previous["nodes"]:
                document["planVersion"] = 1
                document["baseline"] = copy.deepcopy(normalized)
            document.update({"title": title, "nodes": normalized, "updatedAt": at, "planChanges": changes})
            _refresh_review(document)
            self._save(connection, session_id, document)
        return self.snapshot(session_id)

    def update_node(self, session_id: str, node_id: str, status: str | None = None, summary: str | None = None,
                    issue: dict[str, Any] | None = None, evidence: list[dict[str, Any]] | None = None,
                    acceptance: str | None = None, reason: str | None = None,
                    expected_version: int | None = None) -> dict[str, Any]:
        self._require_session(session_id)
        node_id = _identifier(node_id, "nodeId")
        if status is None and summary is None and issue is None and evidence is None and acceptance is None:
            raise ValueError("至少更新状态、摘要、问题、证据或验收标准中的一项")
        if status is not None and status not in PLAN_STATUSES:
            raise ValueError("node.status 无效")
        at = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._load(connection, session_id)
            if not document:
                raise ValueError("请先创建该任务的主要节点计划")
            _expected_version(expected_version, document["planVersion"])
            node = next((node for node in document["nodes"] if node["id"] == node_id), None)
            if not node:
                raise ValueError("找不到该 nodeId")
            acceptance_text = _text(acceptance, "acceptance", 4000) if acceptance is not None else node["acceptance"]
            acceptance_changed = acceptance_text != node["acceptance"]
            existing_issue = None
            normalized_issue = None
            if issue is not None:
                issue_id = issue.get("id") if isinstance(issue, dict) else None
                existing_issue = next((item for item in node["issues"] if item["id"] == issue_id), None)
                normalized_issue = _normalize_issue(issue, existing_issue, at)
            issue_kind_changed = bool(existing_issue and existing_issue["kind"] != normalized_issue["kind"])
            if issue_kind_changed and expected_version is None:
                raise ValueError("修改已有事项类型必须提供 expectedVersion")
            previous_status = node["status"]
            if acceptance_changed or issue_kind_changed:
                reason_text = _text(reason, "reason", 2000, True)
                _record_version(document, at, reason_text)
                document["planChanges"].append({"at": at, "reason": reason_text, "added": [], "removed": []})
            if acceptance_changed:
                node["acceptance"] = acceptance_text
            if status is not None:
                node["status"] = status
            if acceptance_changed and previous_status == "completed":
                if node["status"] == "completed":
                    node["status"] = "pending"
                node["requiresRevalidation"] = True
            elif status == "completed":
                node["requiresRevalidation"] = False
            if summary is not None:
                node["summary"] = _text(summary, "summary", 1000)
            if evidence is not None:
                node["evidence"] = _evidence(evidence)
            if normalized_issue is not None:
                if existing_issue:
                    node["issues"][node["issues"].index(existing_issue)] = normalized_issue
                else:
                    if len(node["issues"]) >= 100:
                        raise ValueError("单个节点的问题数量已达到上限")
                    node["issues"].append(normalized_issue)
            _node_is_completable(node)
            _refresh_review(document)
            document["updatedAt"] = at
            self._save(connection, session_id, document)
        return self.snapshot(session_id)

    def request_change(self, session_id: str, node_id: str | None, message: str,
                       kind: str = "modify", request_id: str | None = None) -> dict[str, Any]:
        """Persist explicit user intent without claiming it changed the plan or host."""
        self._require_session(session_id)
        if kind not in {"modify", "pause_now", "pause_after"}:
            raise ValueError("kind 必须是 modify、pause_now 或 pause_after")
        if node_id is not None:
            node_id = _identifier(node_id, "nodeId")
        if kind != "pause_now" and node_id is None:
            raise ValueError("修改与节点检查点请求必须指定 nodeId")
        message = _text(message, "message", 4000, True)
        request_id = _identifier(request_id or "req_" + uuid.uuid4().hex, "requestId")
        at = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._load(connection, session_id)
            if not document:
                if kind != "pause_now":
                    raise ValueError("请先创建该任务的主要节点计划")
                document = _upgrade_document({"title": "当前任务执行计划", "nodes": [], "updatedAt": at})
            if node_id is not None and not any(node["id"] == node_id for node in document["nodes"]):
                raise ValueError("找不到该 nodeId")
            existing = next((item for item in document["requests"] if item["id"] == request_id), None)
            if existing:
                if (existing["nodeId"], existing["kind"], existing["message"]) != (node_id, kind, message):
                    raise ValueError("该 requestId 已用于另一条请求")
            else:
                if kind == "pause_after":
                    # A newly chosen checkpoint supersedes the previous one, but
                    # its user request remains visible in the historical record.
                    for item in document["requests"]:
                        if item["kind"] == "pause_after" and item["status"] == "pending":
                            item.update({"status": "dismissed", "resolution": "已由新的节点暂停检查点替代", "resolvedAt": at})
                document["requests"].append({
                    "id": request_id, "nodeId": node_id, "kind": kind, "message": message,
                    "status": "pending", "createdAt": at, "planVersion": document["planVersion"],
                })
                document["updatedAt"] = at
                _refresh_review(document)
                self._save(connection, session_id, document)
        return self.snapshot(session_id)

    def resolve_request(self, session_id: str, request_id: str, status: str,
                        resolution: str) -> dict[str, Any]:
        self._require_session(session_id)
        request_id = _identifier(request_id, "requestId")
        if status not in {"applied", "dismissed"}:
            raise ValueError("status 必须是 applied 或 dismissed")
        resolution = _text(resolution, "resolution", 4000, True)
        at = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            document = self._load(connection, session_id)
            if not document:
                raise ValueError("找不到该任务的执行计划")
            request = next((item for item in document["requests"] if item["id"] == request_id), None)
            if not request:
                raise ValueError("找不到该 requestId")
            if request["status"] != "pending":
                if request["status"] != status or request.get("resolution") != resolution:
                    raise ValueError("该请求已经处理；如需新修改请创建新的请求")
            else:
                request.update({"status": status, "resolution": resolution, "resolvedAt": at,
                                "resolvedPlanVersion": document["planVersion"]})
                document["updatedAt"] = at
                _refresh_review(document)
                self._save(connection, session_id, document)
        return self.snapshot(session_id)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        self._require_session(session_id)
        observed = self.logs.read(session_id)
        with self._connect() as connection:
            document = self._load(connection, session_id)
        plan = observed.get("plan")
        source = "reported" if document else "codex-plan" if plan else "none"
        nodes = copy.deepcopy(document["nodes"] if document else plan["nodes"] if plan else [])
        completed = sum(node["status"] == "completed" for node in nodes)
        cancelled = sum(node["status"] == "cancelled" for node in nodes)
        denominator = len(nodes) - cancelled
        updated_at = max(filter(None, [document.get("updatedAt") if document else None,
                                      observed.get("updatedAt")]), default=None)
        value = {"schemaVersion": 1, "sessionId": session_id,
                 "title": document["title"] if document else "当前任务执行计划",
                 "source": source, "updatedAt": updated_at, "nodes": nodes,
                 "progress": {"completed": completed, "total": len(nodes),
                              "percent": round(completed / denominator * 100) if denominator else None,
                              "cancelled": cancelled},
                 "runtime": observed["runtime"],
                 "planChanges": copy.deepcopy(document["planChanges"] if document else observed["planChanges"]),
                 "planVersion": document["planVersion"] if document else 0,
                 "history": copy.deepcopy(document["history"] if document else []),
                 "archivedNodes": copy.deepcopy(document["archivedNodes"] if document else []),
                 "requests": copy.deepcopy(document["requests"] if document else []),
                 "pauseAfterNodeId": document.get("pauseAfterNodeId") if document else None}
        value["revision"] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return value

    def list_sessions(self, limit: int = 20) -> list[dict[str, str]]:
        sessions = self.logs.list_sessions(limit)
        with self._connect() as connection:
            for session in sessions:
                document = self._load(connection, session["sessionId"])
                if document:
                    session["title"] = document["title"]
        return sessions
