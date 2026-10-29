"""Read bounded Codex rollout records without changing the source logs.

Only confirmed structured plan calls become progress. Raw JavaScript tool
wrappers are deliberately not interpreted. Runtime observations are kept
separate from the reported business plan.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any


SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MAX_LINE = 16 * 1024 * 1024
MAX_SCAN = 64 * 1024 * 1024
MAX_FILES = 10_000
MAX_NODES = 100
PLAN_STATUSES = {"pending", "in_progress", "completed", "blocked", "cancelled"}
OBSERVER_TOOLS = {
    "get_execution_panel_update", "list_execution_sessions",
    "open_execution_panel", "show_execution_panel",
    "submit_execution_feedback", "confirm_execution_feedback_delivery",
}


def valid_session_id(value: Any) -> bool:
    return isinstance(value, str) and SESSION_ID.fullmatch(value) is not None


def _bounded_text(value: Any, limit: int = 500) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _safe_file(path: Path, root: Path) -> bool:
    """Reject escapes, links/reparse points and special files on both OSes."""
    try:
        root_resolved = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
        current = path
        while current != root:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
            if current.parent == current:
                return False
            current = current.parent
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    except (OSError, ValueError):
        return False


def _first_meta(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as stream:
            for _ in range(10):
                line = stream.readline(MAX_LINE + 1)
                if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                    return None
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(entry, dict) and entry.get("type") == "session_meta":
                    payload = entry.get("payload")
                    return payload if isinstance(payload, dict) else None
    except OSError:
        pass
    return None


def _output_failed(payload: dict[str, Any]) -> bool:
    if payload.get("isError") or payload.get("is_error"):
        return True
    output = payload.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return output.lstrip().lower().startswith(("error:", "failed:", "cancelled:"))
    if isinstance(output, dict):
        return bool(
            output.get("isError")
            or output.get("is_error")
            or output.get("error")
            or str(output.get("status", "")).lower() in {"error", "failed", "cancelled"}
        )
    return False


def _arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _plan_nodes(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_NODES:
        return None
    result = []
    used = set()
    occurrences: dict[str, int] = {}
    for item in value:
        if not isinstance(item, dict):
            return None
        title = _bounded_text(item.get("step", item.get("title")), 200)
        status_value = item.get("status", "pending")
        if not title or status_value not in PLAN_STATUSES:
            return None
        occurrences[title] = occurrences.get(title, 0) + 1
        identifier = _bounded_text(item.get("id"), 128) or "n_" + hashlib.sha256(
            (title + ":" + str(occurrences[title])).encode("utf-8")
        ).hexdigest()[:12]
        if identifier in used:
            return None
        used.add(identifier)
        result.append({"id": identifier, "title": title, "status": status_value,
                       "summary": "", "issues": []})
    return result


class CodexLogReader:
    """Exact-session discovery plus bounded, newline-safe incremental parsing."""

    def __init__(self, codex_home: Path):
        self.codex_home = Path(codex_home)
        self.roots = [self.codex_home / "sessions", self.codex_home / "archived_sessions"]
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._located: dict[str, tuple[Path, Path]] = {}

    def _files(self) -> list[tuple[Path, Path, int]]:
        files = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for directory, dirs, names in os.walk(root, followlinks=False):
                # Do not traverse junctions even though os.walk on Windows may.
                dirs[:] = [name for name in dirs if not Path(directory, name).is_symlink()
                           and not (getattr(Path(directory, name).lstat(), "st_file_attributes", 0) & 0x400)]
                for name in names:
                    if not name.startswith("rollout-") or not name.endswith(".jsonl"):
                        continue
                    path = Path(directory, name)
                    if _safe_file(path, root):
                        files.append((path, root, path.stat().st_mtime_ns))
                        if len(files) >= MAX_FILES:
                            return sorted(files, key=lambda entry: entry[2], reverse=True)
        return sorted(files, key=lambda entry: entry[2], reverse=True)

    def locate(self, session_id: str) -> tuple[Path, Path, dict[str, Any]] | None:
        if not valid_session_id(session_id):
            return None
        cached_path = self._located.get(session_id)
        if cached_path:
            path, root = cached_path
            if _safe_file(path, root):
                meta = _first_meta(path)
                if meta and meta.get("id", meta.get("session_id")) == session_id:
                    return path, root, meta
            self._located.pop(session_id, None)
        # Usual Codex names carry the exact UUID. Inspect matching headers
        # before walking/stat-ing every unrelated historical rollout.
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.rglob(f"rollout-*{session_id}*.jsonl"):
                if _safe_file(path, root):
                    meta = _first_meta(path)
                    if meta and meta.get("id", meta.get("session_id")) == session_id:
                        self._located[session_id] = (path, root)
                        return path, root, meta
        # A filename narrows candidates; its first metadata ID is still required.
        files = self._files()
        likely = [entry for entry in files if session_id in entry[0].name]
        other = [entry for entry in files if session_id not in entry[0].name]
        for path, root, _ in likely + other:
            meta = _first_meta(path)
            if meta and meta.get("id", meta.get("session_id")) == session_id:
                self._located[session_id] = (path, root)
                return path, root, meta
        return None

    def validate_session(self, session_id: str) -> bool:
        return self.locate(session_id) is not None

    def list_sessions(self, limit: int = 20) -> list[dict[str, str]]:
        result = []
        seen = set()
        for path, _, _ in self._files():
            meta = _first_meta(path)
            if not meta:
                continue
            session_id = meta.get("id", meta.get("session_id"))
            if not valid_session_id(session_id) or session_id in seen:
                continue
            seen.add(session_id)
            result.append({"sessionId": session_id,
                           "title": _bounded_text(meta.get("title"), 200) or session_id})
            if len(result) >= max(1, min(int(limit), 100)):
                break
        return result

    @staticmethod
    def _new_cache(path: Path, info: os.stat_result, meta: dict[str, Any]) -> dict[str, Any]:
        return {"path": str(path), "identity": (info.st_dev, info.st_ino),
                "offset": 0, "observedSize": 0, "mtime": 0, "line": 0,
                "discarding": False,
                "meta": meta, "pending": {}, "confirmed": set(), "plan": None,
                "planChanges": [], "updatedAt": None,
                "runtime": {"status": "unknown", "label": "等待执行状态", "at": None},
                "ownBoundary": not bool(meta.get("parent_thread_id") or meta.get("forked_from_id"))}

    def read(self, session_id: str) -> dict[str, Any]:
        selected = self.locate(session_id)
        if not selected:
            raise ValueError("找不到与当前任务 ID 精确匹配的本地 Codex 日志")
        path, root, meta = selected
        with self._lock:
            if not _safe_file(path, root):
                raise ValueError("会话日志路径不可用")
            info = path.stat()
            cached = self._cache.get(session_id)
            changed_in_place = cached and info.st_size == cached["observedSize"] and info.st_mtime_ns != cached["mtime"]
            if (not cached or cached["path"] != str(path)
                    or cached["identity"] != (info.st_dev, info.st_ino)
                    or info.st_size < cached["observedSize"] or changed_in_place):
                cached = self._new_cache(path, info, meta)
                self._cache[session_id] = cached
            with path.open("rb") as stream:
                stream.seek(cached["offset"])
                scanned = 0
                while scanned < MAX_SCAN:
                    start = stream.tell()
                    line = stream.readline(MAX_LINE + 1)
                    if not line:
                        break
                    scanned += len(line)
                    if cached["discarding"]:
                        cached["offset"] = stream.tell()
                        if line.endswith(b"\n"):
                            cached["discarding"] = False
                            cached["line"] += 1
                        continue
                    if not line.endswith(b"\n"):
                        # Keep an incomplete record for the next poll. Oversized
                        # records are discarded through their actual newline.
                        if len(line) > MAX_LINE:
                            cached["discarding"] = True
                            cached["offset"] = stream.tell()
                            continue
                        stream.seek(start)
                        break
                    cached["offset"] = stream.tell()
                    ordinal_fallback = cached["line"]
                    cached["line"] += 1
                    if len(line) > MAX_LINE:
                        continue
                    try:
                        entry = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(entry, dict):
                        self._consume(cached, entry, session_id, ordinal_fallback)
            cached["observedSize"] = info.st_size
            cached["mtime"] = info.st_mtime_ns
            if len(self._cache) > 64:
                first = next(iter(self._cache))
                if first != session_id:
                    self._cache.pop(first, None)
            return copy.deepcopy({key: cached[key] for key in
                                  ("plan", "planChanges", "updatedAt", "runtime")})

    @staticmethod
    def _consume(cache: dict[str, Any], entry: dict[str, Any], session_id: str, physical_ordinal: int) -> None:
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            return
        meta = cache["meta"]
        history_start = meta.get("subagent_history_start_ordinal")
        ordinal = entry.get("ordinal", physical_ordinal)
        if isinstance(history_start, int) and isinstance(ordinal, int) and ordinal < history_start:
            return
        if entry.get("inherited") is True or entry.get("is_inherited") is True:
            return
        thread_id = payload.get("thread_id", entry.get("thread_id"))
        if thread_id is not None and thread_id != session_id:
            return
        if isinstance(history_start, int) and isinstance(ordinal, int) and ordinal >= history_start:
            cache["ownBoundary"] = True
        if thread_id == session_id:
            cache["ownBoundary"] = True
        if not cache["ownBoundary"]:
            return  # Forks with no reliable boundary remain conservatively unknown.
        kind = entry.get("type")
        timestamp = _bounded_text(entry.get("timestamp"), 64) or None

        def runtime(status: str, label: str) -> None:
            if status in {"running", "recovering"} and any(
                call["name"] == "request_user_input" for call in cache["pending"].values()
            ):
                status, label = "waiting_user", "等待你的回答"
            cache["runtime"] = {"status": status, "label": label, "at": timestamp}
            cache["updatedAt"] = timestamp or cache["updatedAt"]

        if kind == "event_msg":
            event = payload.get("type")
            if event in {"task_started", "task_complete", "turn_aborted"}:
                cache["pending"] = {identifier: call for identifier, call in cache["pending"].items()
                                    if call["name"] != "request_user_input"}
            if event == "task_started":
                runtime("running", "正在执行")
            elif event == "task_complete":
                runtime("turn_finished", "本轮已结束")
            elif event == "turn_aborted":
                runtime("interrupted", "本轮执行已中断")
            elif event == "item_completed":
                item = payload.get("item")
                if isinstance(item, dict):
                    tool_name = item.get("tool", item.get("name", ""))
                    if isinstance(tool_name, str):
                        bare_name = tool_name.split("__")[-1].split(".")[-1]
                        if bare_name in OBSERVER_TOOLS:
                            # Reading this panel is observation, not work. In
                            # particular it must not wake a completed turn or
                            # keep its own "last activity" timestamp fresh.
                            return
                    exit_code = item.get("exit_code")
                    status_value = str(item.get("status", "")).lower()
                    failed = ((isinstance(exit_code, int) and exit_code != 0)
                              or status_value in {"failed", "error"}
                              or _output_failed({"output": item.get("result")}))
                    runtime("recovering" if failed else "running",
                            "工具返回错误，Agent 正在处理" if failed else "正在继续执行")
            return
        if kind != "response_item":
            return
        item_type = payload.get("type")
        call_id = payload.get("call_id")
        if item_type in {"function_call", "custom_tool_call"}:
            name = str(payload.get("name", "")).split(".")[-1]
            if name not in {"update_plan", "request_user_input"} or not isinstance(call_id, str):
                return
            if call_id in cache["confirmed"]:
                return
            args = _arguments(payload.get("arguments", payload.get("input")))
            if name == "update_plan" and (not args or _plan_nodes(args.get("plan")) is None):
                return
            if len(cache["pending"]) >= 1000:
                cache["pending"].pop(next(iter(cache["pending"])))
            cache["pending"][call_id] = {"name": name, "arguments": args, "at": timestamp}
            if name == "request_user_input":
                runtime("waiting_user", "等待你的回答")
        elif item_type in {"function_call_output", "custom_tool_call_output"} and isinstance(call_id, str):
            call = cache["pending"].pop(call_id, None)
            if not call:
                return
            if _output_failed(payload):
                runtime("recovering", "工具返回错误，Agent 正在处理")
                return
            cache["confirmed"].add(call_id)
            if len(cache["confirmed"]) > 5000:
                cache["confirmed"] = {call_id}
            if call["name"] == "request_user_input":
                runtime("running", "已收到回答，正在继续")
                return
            args = call["arguments"]
            nodes = _plan_nodes(args.get("plan"))
            previous = cache["plan"]
            if previous:
                old = [(node["id"], node["title"]) for node in previous["nodes"]]
                new = [(node["id"], node["title"]) for node in nodes]
                if old != new:
                    old_ids = {node["id"] for node in previous["nodes"]}
                    new_ids = {node["id"] for node in nodes}
                    cache["planChanges"].append({"at": timestamp,
                        "reason": _bounded_text(args.get("explanation")) or "Codex 更新了计划",
                        "added": sorted(new_ids - old_ids), "removed": sorted(old_ids - new_ids)})
                    cache["planChanges"] = cache["planChanges"][-100:]
            cache["plan"] = {"nodes": nodes, "at": timestamp or call["at"],
                             "explanation": _bounded_text(args.get("explanation"))}
            cache["updatedAt"] = timestamp or cache["updatedAt"]
