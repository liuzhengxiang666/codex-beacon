# Codex Execution Panel

**See the progress. Reference any step. Stay in the conversation.**

A community-built, open-source MCP sidebar for Codex. Track major plan milestones, distinguish blockers from pending verification, and bring any step into your current chat draft with **@**.

[中文](README.md) · [Installation](docs/installation.md) · [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/liuzhengxiang666/codex-execution-panel/issues)

**Preview · v0.5.1.** The UI is currently Chinese. Windows desktop is the primary manually verified environment; other clients and platforms need testing. This project is not affiliated with OpenAI.

<p align="center"><img src="docs/images/native-sidebar.png" width="380" alt="Actual native Codex sidebar with numbered steps and @ entries"></p>

This is a real native-sidebar capture of the project's own development task. The state examples in the Chinese README run the actual preview UI against synthetic data.

## Features

- A compact, single-column plan with completion count, progress and dependency-aware parallel indicators.
- Separate blockers, decisions, branches and neutral pending verification; unresolved verification still prevents completion.
- An @ entry on **every** node, including pending, completed and cancelled steps.
- Versioned plans, stable node IDs, change reasons and completion evidence.
- Client-aware light/dark themes, reduced-motion support and local SQLite persistence.
- Python standard library backend and vanilla HTML/CSS/JavaScript. No npm build or API key required.

## Quick start

Requirements: Python 3.10+, Codex CLI, and a desktop client supporting local MCP Apps. Node 20+ is only needed for tests.

```sh
git clone https://github.com/liuzhengxiang666/codex-execution-panel.git
cd codex-execution-panel
codex mcp add execution-panel -- python3 -B -u "$(pwd)/server.py" stdio
codex plugin marketplace add .
codex plugin add execution-panel@codex-execution-panel
```

On Windows, use `./scripts/register-mcp.ps1` instead of the `mcp add` command. Reload the MCP connection or restart the client, then invoke the execution-panel skill or ask to open the current task's panel. Full [installation and troubleshooting](docs/installation.md) includes Windows manual commands, updates and uninstall instructions. The skill-only plugin reuses the separately registered MCP server.

## Honest boundaries

Progress is reported by the agent through MCP; supported structured local rollout records may supply runtime observations. This is not an independent scheduler or a universal tool monitor. Automatic sidebar opening and draft import depend on host capabilities. A successful tool call alone does not prove a sidebar opened.

@ appends context only when the host explicitly supports draft import and the current task identity matches. It uses `active + send=false`; you choose whether to send. Otherwise it offers copying. An uncertain acknowledgement is never retried automatically. The panel does not create chats or control native Goal lifecycle.

## Development

```sh
python -B -m unittest discover -s tests -q
node --test tests/test_bridge.mjs tests/test_theme.mjs tests/test_plan_model.mjs tests/test_panel_ui.mjs
```

See [development](docs/development.md), [architecture](docs/architecture.md), [privacy](docs/privacy.md), and [contributing](CONTRIBUTING.md). Compatibility reports, English UI contributions and small focused PRs are welcome. UI PR descriptions must include screenshots of the actual changed interface.

MIT licensed. The bridge is adapted from Codex Trajectory; see [third-party notices](THIRD_PARTY_NOTICES).
