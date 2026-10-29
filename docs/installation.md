# 安装与排障 / Installation

## 环境与兼容性

| 环境 | 状态 |
| --- | --- |
| Windows 桌面客户端，支持本地 stdio MCP 和 MCP Apps | 原生右栏、自动展开、主题和 @ 草稿通道已实测 |
| Python 3.10+ | 后端只用标准库；CI 检查 3.10 与 3.14 |
| Node 20+ | 仅开发测试需要 |
| macOS / Linux 原生宿主 | 提供命令；尚未实机验收右栏和草稿能力 |
| 不支持 MCP Apps 的 CLI / IDE | 不保证有可视面板；不能把文本工具成功当作 UI 可用 |
| Web / 云端会话 | 不支持直接访问此本地 stdio 服务 |

本地日志格式及客户端草稿扩展可能变化。请在反馈时提供具体版本；当前不能承诺跨客户端一致。

## 1. 克隆

```sh
git clone https://github.com/liuzhengxiang666/codex-execution-panel.git
cd codex-execution-panel
```

克隆到你希望存放源码和运行状态的位置。默认数据库位于这个目录的 `state/`；不要把源码放入只读目录。

## 2. 注册一次 MCP

Windows PowerShell：

```powershell
./scripts/register-mcp.ps1
```

脚本定位当前克隆目录与 `python`，注册 `execution-panel`；已有 Codex 配置会先备份到被忽略的 `work/config-backups/`。临时目录跟随系统 `TEMP`，不固定磁盘。它不自动安装 Python，不更改系统策略，不重启客户端。

脚本受执行策略限制时，可以手动运行：

```powershell
$panelPython = (Get-Command python -ErrorAction Stop).Source
$panelServer = (Resolve-Path ./server.py).Path
codex mcp add execution-panel --env PYTHONUTF8=1 --env PYTHONDONTWRITEBYTECODE=1 -- $panelPython -B -u $panelServer stdio
```

macOS / Linux shell：

```sh
codex mcp add execution-panel --env PYTHONUTF8=1 --env PYTHONDONTWRITEBYTECODE=1 -- python3 -B -u "$(pwd)/server.py" stdio
```

查看注册结果：

```sh
codex mcp get execution-panel
```

如已有同名服务，先核对它的路径；不要另注册第二个名字来绕过问题。需要自定义存储时，可在 `stdio` 后加 `--state-dir /absolute/path/to/state`、`--codex-home /absolute/path/to/codex-home`。

## 3. 安装 @ 技能入口

从克隆根目录运行：

```sh
codex plugin marketplace add .
codex plugin add execution-panel@codex-execution-panel
codex plugin list --marketplace codex-execution-panel --json
```

市场名称是 `codex-execution-panel`，插件名称是 `execution-panel`。此技能包不声明 MCP 服务，复用步骤 2 的注册，不会重复启动后台服务。CLI/客户端版本须支持这些插件命令；命令不存在时先检查客户端能力，不能靠复制配置假定安装成功。

重新加载 MCP；宿主没有重载入口时，关闭并重新打开客户端。当前已运行的 Python stdio 进程不会自动加载新的 Python 协议代码。重新打开面板实例可以读取最新 UI 资源。

## 4. 验证实际效果

在同一聊天中调用 @执行面板，或说“使用执行面板执行这个多步任务……”。检查：

- 面板实际出现在右侧，主对话仍可继续；只看到工具调用行不代表右栏打开成功。
- 计划节点来自当前任务，完成更新后百分比刷新。
- 节点旁 @ 可以带入当前草稿；由你手动发送。无草稿能力时只复制，不能新建聊天。

插件技能为已授权的多步执行任务请求默认展示；普通问答不用打开。它不修改用户全局 `AGENTS.md` 或保存第二份偏好库；个人显式选择优先。

## 常见情况

| 情况 | 检查方式 |
| --- | --- |
| 没有 MCP 工具 | `codex mcp get execution-panel` 核对 Python、绝对路径和连接；重新加载连接 |
| 有工具但没右栏 | 确认宿主支持 MCP Apps 与展开模式；从客户端原生入口打开并报告具体版本，不改成默认网页 |
| @ 只能复制 | 宿主未声明草稿扩展、当前聊天绑定未确认，或当前就是独立预览 |
| 显示“未确认” | 先检查主对话草稿，再选择复制；不要反复点击期待自动重试 |
| 进度不动 | 确认 Agent 调用了 `report_execution_update`；无新日志不能推断为卡点 |
| 会话不匹配 | 使用当前聊天的精确 ID；不要猜最近会话，也不要为绕过校验伪造日志 |
| Goal 暂停后状态不一致 | 面板没有独立订阅 Goal 生命周期；以原生 Goal 状态为准 |

## 更新与卸载

更新先 `git pull`，重新加载 MCP；技能有改动时运行 `codex plugin add execution-panel@codex-execution-panel` 刷新。源码目录移动后重新注册服务器路径。更新不会主动清空 `state/`。

卸载前查看当前 CLI：`codex plugin remove --help`、`codex mcp remove --help`。通常可以运行 `codex plugin remove execution-panel@codex-execution-panel` 和 `codex mcp remove execution-panel`。这些操作不替你删除本地 SQLite；是否保留任务历史由你决定。

## 依据

本仓库使用仍被支持的 `.codex-plugin/plugin.json` 兼容清单及 repo marketplace，参见 [OpenAI 官方打包文档](https://developers.openai.com/plugins/build/plugins)。UI 使用 [MCP Apps](https://developers.openai.com/plugins/build/app-quickstart)；本地发布源码不等于上架官方目录。
