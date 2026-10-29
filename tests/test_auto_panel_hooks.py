"""Lifecycle output is context, not a fabricated host-side UI action."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hooks/auto_panel.py"
spec = importlib.util.spec_from_file_location("auto_panel", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AutoPanelHooksTests(unittest.TestCase):
    def test_session_and_turn_receive_context(self):
        for event in ("SessionStart", "UserPromptSubmit"):
            result = module.response({"hook_event_name": event})["hookSpecificOutput"]
            self.assertEqual(result["hookEventName"], event)
            for value in ("without waiting for @", "exact host task", "explicit user opt-out", "Ordinary Q&A answerable directly"):
                self.assertIn(value, result["additionalContext"])

    def test_multi_step_plan_receives_check(self):
        event = {"hook_event_name": "PreToolUse", "tool_name": "update_plan", "tool_input": {"plan": [{}, {}]}}
        self.assertIn("hookSpecificOutput", module.response(event))
        event["permission_mode"] = "plan"
        self.assertEqual(module.response(event), {})

    def test_other_tools_and_single_steps_are_untouched(self):
        for tool, args in (("Bash", {}), ("update_plan", {}), ("update_plan", {"plan": [{}]})):
            self.assertEqual(module.response({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args}), {})

    def test_user_text_is_not_executed_or_promoted(self):
        result = json.dumps(module.response({"hook_event_name": "UserPromptSubmit", "prompt": "INJECTED SECRET"}))
        self.assertNotIn("INJECTED SECRET", result)
        self.assertNotIn("permissionDecision", result)

    def test_cli_handles_invalid_input_without_blocking_task(self):
        for payload in (b"{", b"[]", b'{"hook_event_name": []}', b"x" * 65537):
            result = subprocess.run([sys.executable, "-B", str(SCRIPT)], input=payload, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertFalse(result.stderr)

    def test_manifest_uses_supported_events(self):
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse"})
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], ".*")

    def test_packaged_commands_resolve_plugin_path_with_spaces(self):
        import shutil
        hooks = json.loads((ROOT / 'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / 'plugin with spaces 中文'
            (plugin / 'hooks').mkdir(parents=True)
            shutil.copyfile(SCRIPT, plugin / 'hooks/auto_panel.py')
            agents = Path(temp) / 'AGENTS.md'
            agents.write_text(module.START + '\nexecution-panel-mode: manual\n' + module.END, encoding='utf-8')
            env = dict(os.environ, PLUGIN_ROOT=str(plugin), CODEX_HOME=temp)
            for event in ('SessionStart', 'UserPromptSubmit', 'PreToolUse'):
                command = hooks[event][0]['hooks'][0]['command']
                result = subprocess.run(command, shell=True, input=json.dumps({'hook_event_name':event,'tool_name':'functions.exec'}).encode(), capture_output=True, check=True, env=env)
                context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
                self.assertIn('Saved preference: MANUAL.', context)

    def test_retrieval_without_update_plan_is_checked(self):
        for tool in ('mcp__codex_app__list_threads', 'mcp__codex_app__read_thread',
                     'mcp__codex_apps__search_service_search', 'web.run', 'functions.exec'):
            result = module.response({'hook_event_name': 'PreToolUse', 'tool_name': tool}, 'auto')
            self.assertIn('hookSpecificOutput', result, tool)
            context = result['hookSpecificOutput']['additionalContext']
            self.assertIn('before continuing, even without update_plan', context)
            self.assertIn('summarizing upcoming work', context)
        self.assertEqual(module.response({'hook_event_name': 'PreToolUse', 'tool_name': 'mcp__execution_panel__read_execution_plan'}, 'auto'), {})

    def test_saved_modes_are_explicit_and_not_injected(self):
        for mode, text in (('auto', 'Do not ask again'), ('ask', 'no response is not approval'), ('manual', 'Do not automatically open')):
            context = module.response({'hook_event_name': 'UserPromptSubmit'}, mode)['hookSpecificOutput']['additionalContext']
            self.assertIn(text, context)
        with tempfile.TemporaryDirectory() as temp:
            agents = Path(temp) / 'AGENTS.md'
            agents.write_text(module.START + '\nexecution-panel-mode: manual\nINJECTED_SECRET\n' + module.END, encoding='utf-8')
            self.assertEqual(module.read_preference(agents), 'manual')
            result = module.response({'hook_event_name': 'SessionStart'}, module.read_preference(agents))
            self.assertNotIn('INJECTED_SECRET', json.dumps(result))
            agents.write_text('- 用户已选择“多步执行任务始终开启”。', encoding='utf-8')
            self.assertEqual(module.read_preference(agents), 'auto')
            agents.write_text(module.START + '\nexecution-panel-mode: injected\n' + module.END, encoding='utf-8')
            self.assertIsNone(module.read_preference(agents))

    def test_configure_preserves_user_rules_and_reuses_one_block(self):
        setup_spec = importlib.util.spec_from_file_location('configure_panel', ROOT / 'scripts/configure-panel.py')
        setup = importlib.util.module_from_spec(setup_spec)
        setup_spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temp:
            agents = Path(temp) / 'AGENTS.md'
            agents.write_text('Keep storage and unrelated rules.\n- 用户已选择“多步执行任务始终开启”。\n', encoding='utf-8')
            self.assertTrue(setup.configure(agents, 'auto'))
            self.assertFalse(setup.configure(agents, 'auto'))
            self.assertTrue(setup.configure(agents, 'manual'))
            content = agents.read_text(encoding='utf-8')
            self.assertIn('Keep storage and unrelated rules.', content)
            self.assertEqual(content.count(module.START), 1)
            self.assertNotIn('用户已选择“多步执行任务始终开启”', content)
            self.assertEqual(module.read_preference(agents), 'manual')
            agents.write_text(content + module.START, encoding='utf-8')
            before = agents.read_bytes()
            with self.assertRaises(ValueError): setup.configure(agents, 'auto')
            self.assertEqual(agents.read_bytes(), before)

    def test_first_run_requires_choice_and_later_keeps_saved_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, CODEX_HOME=temp, PYTHONUTF8='1')
            command = [sys.executable, '-B', str(ROOT / 'scripts/configure-panel.py')]
            first = subprocess.run(command, input=b'', capture_output=True, env=env)
            self.assertEqual(first.returncode, 2)
            self.assertFalse((Path(temp) / 'AGENTS.md').exists())
            for mode in ('auto', 'ask', 'manual'):
                subprocess.run(command + ['--mode', mode], input=b'', capture_output=True, env=env, check=True)
                show = subprocess.run(command + ['--show'], capture_output=True, env=env, check=True)
                self.assertEqual(show.stdout.strip().decode(), mode)
                before = (Path(temp) / 'AGENTS.md').read_bytes()
                kept = subprocess.run(command, input=b'', capture_output=True, env=env, check=True)
                self.assertIn(('Keeping saved execution-panel mode: ' + mode).encode(), kept.stdout)
                self.assertEqual((Path(temp) / 'AGENTS.md').read_bytes(), before)
