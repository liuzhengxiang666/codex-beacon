"""Small local MCP surface for a read-only viewer and explicit progress reports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

VERSION = "0.5.3"
RESOURCE = "ui://codex-execution-panel/panel.html"
INSTRUCTIONS = (
    "When implementing a user-approved plan, initialize set_execution_plan once with 4-8 "
    "major outcome-oriented nodes and stable IDs, preserving all acceptance criteria. "
    "Use the current task ID from tool metadata, or supply the exact sessionId. "
    "Record actual plan dependencies with each node's optional dependsOn array of stable IDs; "
    "an explicit empty array means no prerequisite, omission preserves the previous sequence. "
    "Never invent parallel work or confuse an alternative route with parallel execution. "
    "Update report_execution_update when a major node starts, completes, or changes. "
    "Report real blockers and meaningful route branches promptly with brief factual reasons. "
    "Use verification issues for pending acceptance, not blocker; unresolved verification still "
    "prevents completion. Reclassifying an existing issue requires reason and expectedVersion. "
    "Use verification for pending acceptance that is not an execution blocker; resolve it before "
    "marking its node completed. Changing an existing issue kind requires reason and expectedVersion. "
    "The side panel refreshes independently; keep normal chat output useful and concise. "
    "Never turn tool-call lists into the main plan, invent progress, infer a blocker from silence, "
    "or silently change requirements. A tool failure alone is not a blocked business node. "
    "Resolve an issue with the same issue ID and a resolution. For a branch, record alternatives "
    "and the selected route. Ordinary implementation choices may proceed within authorization; "
    "use decision issues only when user input is actually required. Finished turns do not finish "
    "the whole plan. The panel is observational: a displayed branch selection is a record, not "
    "a new permission or a message sent to the agent. Only mark a node completed after validation. "
    "If the approved plan changes, preserve stable IDs and supply a reason to set_execution_plan. "
    "When a native Goal is active, KEEP that same Goal as the sole execution/lifecycle authority. "
    "This panel visualizes its Plan; never create a second Goal, infer Goal completion from 100%, "
    "or change native Goal state by editing panel data. All new instructions come from the MAIN "
    "chat; the sidebar has numbered nodes and no editor or pause-submit button. "
    "Every node has an explicit @ entry regardless of status; issue entries can reference blockers, decisions, branches or verification. An explicit intervention click may append context to the active draft with send=false "
    "when the host advertises that capability and the task identity matches; otherwise copy only. "
    "Use the execution-panel plugin mention as the chat entrypoint. Read_execution_plan provides "
    "the current Plan; map user-facing 01, 02, 03 numbers to its nodes in display order and use "
    "stable node IDs for updates. Verify any old Plan version before interpreting a number. "
    "Explain modification impact, preserve old versions, and reopen affected completed nodes. "
    "Handle any legacy pending request once and record resolution with resolve_execution_request. "
    "A pause_after checkpoint means stop BEFORE the next major node; "
    "use native Goal pause when the user has explicitly requested it. Pause requests are not "
    "proof that the host stopped. Show real validation evidence when completing nodes."
)


def obj(properties=None, required=None):
    result = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        result["required"] = required
    return result


def string(description="", **kw):
    return {"type": "string", "description": description, **kw}


SESSION = string("Exact current Codex task UUID. May be omitted only when host task metadata is available.")
STATUS = string(enum=["pending", "in_progress", "completed", "blocked", "cancelled"])
EVIDENCE = {"type": "array", "maxItems": 30, "items": obj({"label": string(), "url": string(), "detail": string()}, ["label"])}
EXPECTED_VERSION = {"type": "integer", "minimum": 1, "description": "Plan version shown before the edit; rejects stale structural changes."}
ISSUE = obj({
    "id": string("Stable ID: reuse it when resolving this issue."),
    "kind": string(enum=["blocker", "branch", "decision", "verification"]),
    "title": string(), "detail": string(),
    "options": {"type": "array", "items": string(), "maxItems": 8},
    "selected": string("Chosen option, when a route has actually been selected."),
    "status": string(enum=["open", "resolved"]), "resolution": string(),
}, ["id", "kind", "title", "status"])


def ui_meta(app_only=False, entrypoint=False):
    meta = {"ui": {"resourceUri": RESOURCE}}
    if app_only:
        meta["ui"]["visibility"] = ["app"]
        meta["openai/visibility"] = "private"
    if entrypoint:
        meta["openai/ui"] = {"entrypoints": [{"type": "thread"}]}
    return meta


def definitions():
    readonly = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
    local_write = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
    return [
        {"name": "open_execution_panel", "title": "Beacon 执行进度", "description": "Open the current task execution panel.",
         "inputSchema": obj(), "annotations": readonly, "_meta": ui_meta(True, True)},
        {"name": "show_execution_panel", "title": "显示 Beacon 执行面板", "description": "Show the execution panel for the exact current task.",
         "inputSchema": obj({"sessionId": SESSION}), "annotations": readonly, "_meta": ui_meta()},
        {"name": "list_execution_sessions", "description": "Choose a local task when the host supplies no task identity.",
         "inputSchema": obj(), "annotations": readonly, "_meta": {"ui": {"visibility": ["app"]}, "openai/visibility": "private"}},
        {"name": "get_execution_panel_update", "description": "Refresh the open panel without starting a model turn.",
         "inputSchema": obj({"sessionId": SESSION, "revision": string()}, ["sessionId"]),
         "annotations": readonly, "_meta": {"ui": {"visibility": ["app"]}, "openai/visibility": "private"}},
        {"name": "set_execution_plan", "title": "同步主要执行节点",
         "description": "Initialize 4-8 major nodes from an approved plan. Preserve stable IDs across turns. Later plan changes require a reason; this writes only plugin state.",
         "inputSchema": obj({"sessionId": SESSION, "title": string(), "reason": string(), "expectedVersion": EXPECTED_VERSION,
             "nodes": {"type": "array", "minItems": 1, "maxItems": 20,
                       "items": obj({"id": string(), "title": string(), "status": STATUS, "summary": string(), "acceptance": string(), "evidence": EVIDENCE,
                                     "dependsOn": {"type": "array", "maxItems": 20, "items": string("Stable prerequisite node ID. Empty array means no prerequisite; omitted means previous node.")}}, ["id", "title"]) }}, ["title", "nodes"]),
         "annotations": local_write},
        {"name": "report_execution_update", "title": "更新执行节点",
         "description": "Record one major node's actual progress, a blocker, decision, route branch, or pending verification. Resolve using the same issue ID; changing an existing issue kind requires reason and expectedVersion. This does not send messages or execute a branch.",
         "inputSchema": obj({"sessionId": SESSION, "nodeId": string(), "status": STATUS, "summary": string(), "issue": ISSUE,
                             "evidence": EVIDENCE, "acceptance": string(), "reason": string(), "expectedVersion": EXPECTED_VERSION}, ["nodeId"]),
         "annotations": local_write},
        {"name": "read_execution_plan", "description": "Read the current Plan, versions, completion evidence and pending user feedback without opening a viewer.",
         "inputSchema": obj({"sessionId": SESSION}), "annotations": readonly},
        {"name": "resolve_execution_request", "description": "Record how an explicit user feedback request was handled; does not pause or resume a Goal.",
         "inputSchema": obj({"sessionId": SESSION, "requestId": string(), "status": string(enum=["applied", "dismissed"]), "resolution": string()}, ["requestId", "status", "resolution"]),
         "annotations": local_write},
        {"name": "submit_execution_feedback", "description": "Store feedback explicitly submitted in this panel and offer a same-chat message handoff. Never claims execution has stopped.",
         "inputSchema": obj({"sessionId": SESSION, "nodeId": string(), "message": string(), "kind": string(enum=["modify", "pause_now", "pause_after"]), "requestId": string()}, ["sessionId", "message", "kind", "requestId"]),
         "annotations": local_write, "_meta": {"ui": {"visibility": ["app"]}, "openai/visibility": "private"}},
        {"name": "confirm_execution_feedback_delivery", "description": "Record a host acknowledgement for a user message. Sent does not mean applied or paused.",
         "inputSchema": obj({"sessionId": SESSION, "requestId": string(), "status": string(enum=["sent", "failed", "unconfirmed"])}, ["sessionId", "requestId", "status"]),
         "annotations": local_write, "_meta": {"ui": {"visibility": ["app"]}, "openai/visibility": "private"}},
    ]


def validate(value, schema, path="arguments"):
    """Validate the deliberately small schema subset used by this server."""
    typ = schema.get("type")
    if typ == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"Missing {path}.{key}")
        for key, child in value.items():
            if key not in schema.get("properties", {}):
                raise ValueError(f"Unknown argument {path}.{key}")
            validate(child, schema["properties"][key], f"{path}.{key}")
    elif typ == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError(f"{path} has an invalid number of entries")
        for child in value:
            validate(child, schema["items"], path + "[]")
    elif typ == "string":
        if not isinstance(value, str) or len(value) > 4000:
            raise ValueError(f"{path} must be text of at most 4000 characters")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"Unsupported {path}")
    elif typ == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or value < schema.get("minimum", 0):
            raise ValueError(f"{path} must be a valid integer")


def result(data, is_error=False):
    # Keep large UI payloads out of the model-facing text response.
    return {"content": [{"type": "text", "text": "执行状态已同步。" if not is_error else data.get("error", "Operation failed")}],
            "structuredContent": data, **({"isError": True} if is_error else {})}


class PanelAPI:
    def __init__(self, service, root: Path):
        self.service, self.root = service, root
        self.tools = {tool["name"]: tool for tool in definitions()}
        self.interactions = None
        if hasattr(service, "db_path"):
            from .interactions import Interactions
            self.interactions = Interactions(service)

    def snapshot(self, session_id):
        value = self.service.snapshot(session_id)
        if self.interactions:
            value = self.interactions.enrich(value)
            self.revision(value)
        return value

    @staticmethod
    def revision(value):
        payload = {key: item for key, item in value.items() if key not in {"revision", "delivery", "handoff"}}
        value["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return value

    def session(self, args, meta):
        ids = [meta.get(key) for key in ("thread_id", "threadId") if key in meta]
        if ids and (any(not isinstance(x, str) for x in ids) or len(set(ids)) != 1):
            raise ValueError("Host task identity is conflicting or invalid")
        explicit = args.get("sessionId")
        if explicit and ids and explicit != ids[0]:
            raise ValueError("Requested task does not match the host task")
        session_id = explicit or (ids[0] if ids else None)
        if not session_id or not self.service.validate_session(session_id):
            raise ValueError("A verified, exact local task ID is required")
        return session_id

    def call(self, name, args, meta=None, *, allow_handoff=True):
        if name not in self.tools:
            raise ValueError("Unknown tool")
        validate(args, self.tools[name]["inputSchema"])
        meta = meta or {}
        if name == "list_execution_sessions":
            return result({"sessions": self.service.list_sessions()})
        if name in ("open_execution_panel", "show_execution_panel"):
            try:
                sid = self.session(args, meta)
            except ValueError:
                return result({"viewerState": "select-session", "sessions": self.service.list_sessions()})
            snapshot = self.snapshot(sid)
            snapshot["viewerBinding"] = {"sessionId": sid, "verified": any(key in meta for key in ("thread_id", "threadId"))}
            return result(snapshot)
        # UI reads may select an exact session after the host offered no identity.
        if name == "get_execution_panel_update":
            sid = args["sessionId"]
            if not self.service.validate_session(sid):
                raise ValueError("The selected local task cannot be verified")
            snapshot = self.snapshot(sid)
            host_ids = [meta.get(key) for key in ("thread_id", "threadId") if key in meta]
            snapshot["viewerBinding"] = {"sessionId": sid, "verified": bool(host_ids) and all(value == sid for value in host_ids)}
            if args.get("revision") == snapshot["revision"]:
                return result({"unchanged": True, "revision": snapshot["revision"], "runtime": snapshot["runtime"], "viewerBinding": snapshot["viewerBinding"], **({"host": snapshot["host"]} if "host" in snapshot else {})})
            return result(snapshot)
        sid = self.session(args, meta)
        if name == "read_execution_plan":
            return result(self.snapshot(sid))
        if name == "set_execution_plan":
            extra = {"expected_version": args["expectedVersion"]} if "expectedVersion" in args else {}
            saved = self.service.set_plan(sid, args["title"], args["nodes"], args.get("reason"), **extra)
            return result(self.snapshot(sid) if self.interactions else saved)
        if name == "report_execution_update":
            extra = {key: args[key] for key in ("evidence", "acceptance", "reason") if key in args}
            if "expectedVersion" in args:
                extra["expected_version"] = args["expectedVersion"]
            saved = self.service.update_node(sid, args["nodeId"], args.get("status"), args.get("summary"), args.get("issue"), **extra)
            return result(self.snapshot(sid) if self.interactions else saved)
        if name == "resolve_execution_request":
            self.service.resolve_request(sid, args["requestId"], args["status"], args["resolution"])
            return result(self.snapshot(sid))
        if name == "submit_execution_feedback":
            if not self.interactions:
                raise ValueError("This server does not provide feedback storage")
            bound_to_host = any(key in meta for key in ("thread_id", "threadId"))
            can_handoff = allow_handoff and bound_to_host
            blocked_reason = "请求已记录；宿主未提供可核对的当前聊天身份，尚未发送给 Codex。" if allow_handoff and not bound_to_host else None
            value = self.interactions.submit(sid, args.get("nodeId"), args["message"], args["kind"], args["requestId"], can_handoff, blocked_reason)
            return result(self.revision(value))
        if name == "confirm_execution_feedback_delivery":
            if not self.interactions:
                raise ValueError("This server does not provide feedback storage")
            return result(self.revision(self.interactions.confirm_delivery(sid, args["requestId"], args["status"])))
        raise ValueError("Unknown tool")

    def resource(self):
        assets = self.root / "assets"
        html = (assets / "panel.html").read_text(encoding="utf-8")
        html = html.replace('<link rel="stylesheet" href="panel.css">', "<style>" + (assets / "panel.css").read_text(encoding="utf-8") + "</style>")
        for name in ("panel-theme.js", "mcp-app-bridge.js", "plan-model.js", "panel.js"):
            script = (assets / name).read_text(encoding="utf-8").replace("__TRAJECTORY_VERSION__", VERSION)
            html = html.replace(f'<script src="{name}"></script>', "<script>" + script.replace("</script", "<\\/script") + "</script>")
        return {"contents": [{"uri": RESOURCE, "mimeType": "text/html;profile=mcp-app", "text": html,
                              "_meta": {
                                  "ui": {"prefersBorder": False, "csp": {"connectDomains": [], "resourceDomains": []}},
                                  # Declare this before initialization; omitting openai/ui
                                  # preserves the host's legacy inline-only default.
                                  "openai/ui": {"availableDisplayModes": ["fullscreen"],
                                                "preferredDisplayMode": "fullscreen"},
                              }}]}

    def dispatch(self, method, params):
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": list(self.tools.values())}
        if method == "tools/call":
            try:
                return self.call(params.get("name"), params.get("arguments", {}), params.get("_meta"))
            except (ValueError, KeyError, TypeError) as error:
                return result({"error": str(error)[:500]}, True)
        if method == "resources/list":
            return {"resources": [{"uri": RESOURCE, "name": "Beacon 执行进度", "mimeType": "text/html;profile=mcp-app"}]}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            if params.get("uri") != RESOURCE:
                raise ValueError("Unknown UI resource")
            return self.resource()
        raise LookupError("Unknown method")


def loads_strict(raw):
    def pairs(items):
        value = {}
        for key, child in items:
            if key in value:
                raise ValueError("Duplicate JSON property")
            value[key] = child
        return value
    def constant(_):
        raise ValueError("Non-finite JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def serve_stdio(api, source, sink):
    initialized = False
    negotiated = False
    while True:
        raw = source.readline(1_048_577)
        if not raw:
            return
        request_id = None
        try:
            if len(raw) > 1_048_576:
                raise ValueError("Message exceeds 1 MiB")
            try:
                msg = loads_strict(raw)
            except (ValueError, UnicodeDecodeError):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
                sink.write((json.dumps(response) + "\n").encode("utf-8"))
                sink.flush()
                continue
            if not isinstance(msg, dict):
                raise ValueError("Expected a JSON-RPC object")
            request_id = msg.get("id")
            if request_id is not None and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
                raise ValueError("Invalid request ID")
            method = msg.get("method")
            params = msg.get("params", {})
            # Notifications never receive responses, even when malformed.
            if "id" not in msg:
                if method == "notifications/initialized" and isinstance(params, dict):
                    initialized = negotiated
                continue
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ValueError("Invalid method or params")
            if request_id is None:
                raise ValueError("Request ID must not be null")
            if method == "initialize":
                negotiated = True
                value = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "resources": {}},
                         "serverInfo": {"name": "codex-execution-panel", "version": VERSION}, "instructions": INSTRUCTIONS}
            elif not initialized:
                raise ValueError("Initialize the MCP connection first")
            else:
                value = api.dispatch(method, params)
            response = {"jsonrpc": "2.0", "id": request_id, "result": value}
        except Exception as error:
            code = -32601 if isinstance(error, LookupError) else -32602 if isinstance(error, (ValueError, TypeError)) else -32603
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": code, "message": str(error)[:500] if code != -32603 else "Local panel operation failed"}}
        sink.write((json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        sink.flush()
