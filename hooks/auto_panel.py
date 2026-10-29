"""Lifecycle context for the execution panel; never claims to open host UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

START = '<!-- execution-panel-preference:start -->'
END = '<!-- execution-panel-preference:end -->'
MODES = {'auto', 'ask', 'manual'}


def preference_path():
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'AGENTS.md'


def read_preference(path=None):
    """Read only a whitelisted mode; never promote arbitrary AGENTS text."""
    try:
        with (Path(path) if path else preference_path()).open('rb') as source:
            raw = source.read(262145)
        if len(raw) > 262144:
            return None
        content = raw.decode('utf-8-sig')
    except (OSError, UnicodeError):
        return None
    if START in content or END in content:
        if content.count(START) != 1 or content.count(END) != 1:
            return None
        block = content.split(START, 1)[1].split(END, 1)[0]
        match = re.search(r'^execution-panel-mode: (auto|ask|manual)\s*$', block, re.M)
        return match[1] if match else None
    # Preserve the existing explicit user preference without asking again.
    if '用户已选择“多步执行任务始终开启”' in content:
        return 'auto'
    return None


CONTEXT = (
    "Execution-panel lifecycle check: classify by actual work, not request title, verb, or whether code changes. "
    "Multi-step includes retrieval, research, cross-chat reading, source verification, comparison, "
    "summarization and scheduling when they require gathering evidence then synthesizing a result. "
    "For example, collecting recent chats then summarizing upcoming work is multi-step. "
    "Before the first substantive retrieval, evaluate the expected workflow; if a simple answer expands "
    "into multi-source or multi-stage work, re-evaluate before continuing, even without update_plan. "
    "When the saved preference authorizes opening (AUTO, or explicit approval under ASK/MANUAL), "
    "for an authorized multi-step task use the existing execution-panel MCP without waiting for @: "
    "read_execution_plan for this exact host task, initialize or update real major outcome nodes if needed, "
    "then show_execution_panel once if the native sidebar is not already open. "
    "Keep normal conversation output flowing. A hook or successful tool result does not prove the sidebar opened; "
    "verify actual host presentation when available, and report unavailable capabilities honestly. "
    "Respect an explicit user opt-out or closed panel. Ordinary Q&A answerable directly, "
    "a brief planning discussion without investigation, and a simple single-step operation do not qualify. "
    "Do not create a chat, Agent, Goal, extra MCP service, or browser fallback. "
    "Reuse the current task's plan and stable IDs. Use the execution-panel skill for the full contract."
)

POLICIES = {
    'auto': 'Saved preference: AUTO for actual multi-step tasks. Do not ask again for every task. ',
    'manual': 'Saved preference: MANUAL. Do not automatically open the panel; use it only on explicit user request. ',
    'ask': 'Saved preference: ASK. Ask once at the start of each qualifying task before opening; no response is not approval. ',
    None: 'Preference is not configured. At the first qualifying task offer AUTO (recommended), ASK, or MANUAL in the main chat, and persist the explicit choice using scripts/configure-panel.py. Do not assume consent or ask on every tool call. ',
}


def response(event, preference=None):
    name = event.get("hook_event_name")
    if not isinstance(name, str) or name not in {"SessionStart", "UserPromptSubmit", "PreToolUse"}:
        return {}
    if name == "PreToolUse":
        if event.get("permission_mode") == "plan":
            return {}
        tool = event.get('tool_name', '')
        if not isinstance(tool, str) or 'execution_panel' in tool:
            return {}
        if tool == 'update_plan':
            args = event.get('tool_input')
            if not isinstance(args, dict) or not isinstance(args.get('plan'), list) or len(args['plan']) < 2:
                return {}
        elif not re.search(r'search|read_thread|list_threads|find_pages|read_page|fetch|web.*run|(?:^|[._])exec$', tool, re.I):
            return {}
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": POLICIES.get(preference, POLICIES[None]) + CONTEXT}}


def main():
    # Hook input is untrusted; never interpolate it into shell commands or instructions.
    raw = sys.stdin.buffer.read(65537)
    try:
        if len(raw) > 65536:
            raise ValueError("oversize")
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("not an object")
        result = response(event, read_preference())
    except (ValueError, UnicodeDecodeError):
        result = {}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
