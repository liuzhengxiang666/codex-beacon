"""User feedback receipts for the Plan panel; no Goal or execution-control writes."""
from __future__ import annotations

import copy
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Interactions:
    def __init__(self, service):
        self.service = service
        with service._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS feedback_delivery (session_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(session_id,request_id))")

    @staticmethod
    def _find(snapshot, request_id):
        item = next((item for item in snapshot.get("requests", []) if item["id"] == request_id), None)
        if item is None:
            raise ValueError("找不到该修改请求")
        return item

    def _receipts(self, session_id):
        with self.service._connect() as db:
            rows = db.execute("SELECT request_id,status,message,updated_at FROM feedback_delivery WHERE session_id=?", (session_id,)).fetchall()
        return {row[0]: {"status": row[1], "message": row[2], "updatedAt": row[3]} for row in rows}

    def enrich(self, snapshot):
        result = copy.deepcopy(snapshot)
        receipts = self._receipts(snapshot["sessionId"])
        for item in result.get("requests", []):
            if item["id"] in receipts:
                item["delivery"] = receipts[item["id"]]
        pending = [item for item in result.get("requests", []) if item["status"] == "pending"]
        by_id = {node["id"]: node for node in result["nodes"]}
        immediate = [item for item in pending if item["kind"] == "pause_now"]
        checkpoints = [item for item in pending if item["kind"] == "pause_after" and by_id.get(item["nodeId"], {}).get("status") == "completed"]
        result["executionDirective"] = {"pauseRequested": bool(immediate), "checkpointReached": bool(checkpoints),
                                        "requestIds": [item["id"] for item in immediate + checkpoints]}
        runtime = result.get("runtime", {})
        if runtime.get("status") in {"turn_finished", "interrupted", "idle"}:
            control = {"status": "idle", "message": "当前轮次已结束。后台命令请在 Codex 原生终端中查看。"}
        elif immediate or checkpoints:
            control = {"status": "requested", "message": "暂停请求尚待处理；需要立即停止请使用 Codex 原生停止按钮。"}
        else:
            control = {"status": "unavailable", "message": "暂停通过聊天协作请求处理；立即停止请用 Codex 原生停止按钮。"}
        result["host"] = {"available": False, "control": control}
        return result

    @staticmethod
    def prompt(snapshot, item):
        node = next((node for node in snapshot["nodes"] if node["id"] == item["nodeId"]), None)
        target = f"节点「{node['title']}」（{node['id']}）" if node else "当前 Plan"
        if item["kind"] == "pause_after":
            action = "请完成这个节点并验证后停下，在我继续之前不要开始下一主要节点。"
        elif item["kind"] == "pause_now":
            action = "请在当前动作的安全边界停止继续修改，保留已有工作，先处理我的纠偏要求。"
        else:
            action = "请先解释这项修改的影响，再更新 Plan 版本；受影响的已完成项需要重新验证。"
        return (f"我在 Plan 执行面板中提交了请求 {item['id']}，针对{target}。\n"
                f"任务 sessionId：{snapshot['sessionId']}。请先核对当前聊天 ID；不匹配时不要执行此请求。\n"
                f"{action}\n我的要求：{item['message']}\n"
                "请读取当前执行计划中的同 ID 请求，处理后记录结果；重复收到同一请求请勿重复执行。"
                "如果当前已有 Goal，请沿用同一个原生 Goal 及其启停机制，不创建新 Goal。"
                "这个面板只同步对应的主要节点、修改记录和完成依据，不代表 Goal 已完成。")

    def submit(self, session_id, node_id, message, kind, request_id, allow_handoff=True, handoff_blocked_reason=None):
        snapshot = self.service.request_change(session_id, node_id, message, kind, request_id)
        item = self._find(snapshot, request_id)
        offer = False
        with self.service._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT status,message FROM feedback_delivery WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
            if previous is None:
                status = "offered" if allow_handoff else "pending"
                note = "请求已记录，等待向当前聊天提交。" if allow_handoff else handoff_blocked_reason or "预览仅保存修改请求，尚未发送给 Codex。"
                db.execute("INSERT INTO feedback_delivery VALUES (?,?,?,?,?)", (session_id, request_id, status, note, _now()))
                offer = allow_handoff
            elif previous[0] in {"failed", "pending"} and allow_handoff:
                db.execute("UPDATE feedback_delivery SET status='offered',message=?,updated_at=? WHERE session_id=? AND request_id=?", ("正在重新提交这条请求。", _now(), session_id, request_id))
                offer = True
            # An offered request may already have been sent by another open view.
            # Never automatically hand it off twice after an ambiguous failure.
        result = self.enrich(snapshot)
        receipt = self._receipts(session_id)[request_id]
        result["delivery"] = receipt
        if offer:
            result["handoff"] = {"requestId": request_id, "prompt": self.prompt(snapshot, item)}
        return result

    def confirm_delivery(self, session_id, request_id, status):
        if status not in {"sent", "failed", "unconfirmed"}:
            raise ValueError("请求投递状态无效")
        self._find(self.service.snapshot(session_id), request_id)
        labels = {"sent": "请求已发送到当前聊天，等待 Codex 处理。", "failed": "请求未能发送，修改草稿已保留。", "unconfirmed": "请求是否送达尚未确认，请勿反复提交。"}
        with self.service._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM feedback_delivery WHERE session_id=? AND request_id=?", (session_id, request_id)).fetchone()
            if row is None:
                raise ValueError("该请求尚未交给聊天宿主")
            if row[0] != "sent":
                db.execute("UPDATE feedback_delivery SET status=?,message=?,updated_at=? WHERE session_id=? AND request_id=?", (status, labels[status], _now(), session_id, request_id))
        return self.enrich(self.service.snapshot(session_id))
