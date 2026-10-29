"""Codex MCP execution panel. Python standard library only; state stays beside this file."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
from panel.protocol import PanelAPI, serve_stdio, loads_strict
from panel.state import PanelService


def preview(api, port, write_token=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed_origin(self):
            allowed = (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
            return self.headers.get("Host", "") in allowed and self.headers.get("Origin") in (None, *["http://" + x for x in allowed])

        def json_reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            supplied = self.headers.get("X-Execution-Panel-Token", "")
            if not self.allowed_origin() or not write_token or not hmac.compare_digest(supplied, write_token):
                self.json_reply(403, {"error": "预览修改需要当前本地会话的写入令牌。"})
                return
            if urlparse(self.path).path != "/api/feedback":
                self.json_reply(404, {"error": "Unknown endpoint"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536 or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    raise ValueError("Invalid request body")
                args = loads_strict(self.rfile.read(size))
                outcome = api.call("submit_execution_feedback", args, allow_handoff=False)
                self.json_reply(400 if outcome.get("isError") else 200, outcome["structuredContent"])
            except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
                self.json_reply(400, {"error": str(error)[:500]})

        def do_GET(self):
            if not self.allowed_origin():
                self.send_error(403)
                return
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/state":
                    args = {key: query[key][0] for key in ("sessionId", "revision") if key in query}
                    outcome = api.call("get_execution_panel_update", args)
                    body, mime = json.dumps(outcome["structuredContent"], ensure_ascii=False).encode("utf-8"), "application/json"
                elif parsed.path == "/api/sessions":
                    body, mime = json.dumps({"sessions": api.service.list_sessions()}, ensure_ascii=False).encode("utf-8"), "application/json"
                elif parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                elif parsed.path in ("/", "/index.html", "/panel.html"):
                    body, mime = (ROOT / "assets/panel.html").read_bytes(), "text/html; charset=utf-8"
                elif parsed.path in ("/panel.js", "/panel.css", "/panel-theme.js", "/mcp-app-bridge.js", "/plan-model.js"):
                    body = (ROOT / "assets" / parsed.path[1:]).read_bytes()
                    mime = "text/css" if parsed.path.endswith(".css") else "text/javascript"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, KeyError, TypeError) as error:
                body = json.dumps({"error": str(error)[:500]}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Execution panel preview: http://127.0.0.1:{port}/?preview=1", flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["stdio", "preview", "set-plan", "update", "snapshot"], default="stdio")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--state-dir", type=Path, default=ROOT / "state")
    parser.add_argument("--session-id")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--write-token-file", type=Path, help="Enable state-only preview feedback with a local token file. Never sends chat messages.")
    args = parser.parse_args()
    service = PanelService(args.state_dir, args.codex_home)
    api = PanelAPI(service, ROOT)
    if args.action == "stdio":
        serve_stdio(api, sys.stdin.buffer, sys.stdout.buffer)
    elif args.action == "preview":
        write_token = None
        if args.write_token_file:
            args.write_token_file.parent.mkdir(parents=True, exist_ok=True)
            if args.write_token_file.exists():
                write_token = args.write_token_file.read_text(encoding="utf-8").strip()
            else:
                write_token = secrets.token_urlsafe(32)
                args.write_token_file.write_text(write_token, encoding="utf-8")
            if not 20 <= len(write_token) <= 200:
                parser.error("Invalid preview write-token file")
        preview(api, args.port, write_token)
    else:
        if not args.session_id:
            parser.error("--session-id is required")
        if args.action == "snapshot":
            value = service.snapshot(args.session_id)
        else:
            if not args.input:
                parser.error("--input is required")
            payload = loads_strict(args.input.read_text(encoding="utf-8-sig"))
            payload["sessionId"] = args.session_id
            name = "set_execution_plan" if args.action == "set-plan" else "report_execution_update"
            value = api.call(name, payload)["structuredContent"]
        print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
