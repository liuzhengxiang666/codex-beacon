# 安装与排障 / Installation

产品名为 **Codex Beacon**（原 Codex Execution Panel），显示名为 **Beacon 执行面板**。仓库地址改为 `liuzhengxiang666/codex-beacon`；兼容标识仍为 MCP `execution-panel`、marketplace `codex-execution-panel`、插件/技能 `execution-panel`。无需改已有源码文件夹名、资源 URI 或迁移 `state/`。

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
git clone https://github.com/liuzhengxiang666/codex-beacon.git
cd codex-beacon
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

## 4. 首次设置与实际触发

Windows 注册脚本会在首次运行时提供一次默认方式选择：多步任务自动开（推荐）、每次询问、手动开；已有设置会保留。也可显式运行 `./scripts/register-mcp.ps1 -PanelMode auto`。其他平台或以后更改设置：

```sh
python scripts/configure-panel.py --mode auto
python scripts/configure-panel.py --show
```

`auto`、`ask`、`manual` 保存在现有用户级 `AGENTS.md`（尊重 `CODEX_HOME`）；修改前备份到被忽略的 `work/config-backups/`，不会修改仓库规则。无明确选择时不替用户默认同意。

多步按实际工作判断：包括先读取多个聊天、核对进度，再汇总工作安排；也包括资料检索、比较与研究。直接可答的简短问答不自动开。即使不调用 `update_plan`，检索工具前也会重新提醒检查。

0.5.3 包含 SessionStart、UserPromptSubmit 和 PreToolUse 生命周期 hooks；请在 CLI `/hooks` 正常审核信任。更新定义需重新审核，脚本不会手写信任哈希。hook 注入执行提醒，由 Agent 调用既有 MCP；不等于宿主强制挂载，未实际出现右栏时仍算未通过，不承诺所有客户端必定自动展开。

### 验证实际效果

保存 auto 后，不用 @，在同一聊天要求“读取最近多个聊天，核对待办并总结未来几天安排”。检查：

- 面板实际出现在右侧，主对话仍可继续；只看到工具调用行不代表右栏打开成功。
- 计划节点来自当前任务，完成更新后百分比刷新。
- 节点旁 @ 可以带入当前草稿；由你手动发送。无草稿能力时只复制，不能新建聊天。

技能和 hook 复用同一用户偏好；配置脚本只在明确选择后保存用户级 AGENTS.md，不创建第二份偏好库。当前聊天显式关闭优先。

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

已有克隆可继续使用原目录，不要为了更名移动运行中的源码。建议更新远端后拉取：

```sh
git remote set-url origin https://github.com/liuzhengxiang666/codex-beacon.git
git pull --ff-only
codex plugin marketplace add .
codex plugin add execution-panel@codex-execution-panel
```

新的 hooks 定义需要在 `/hooks` 重新审核信任。是否自动请求面板以原有偏好为准；更新不会替你改成 auto。

更新先 `git pull`，重新加载 MCP；技能有改动时运行 `codex plugin add execution-panel@codex-execution-panel` 刷新。源码目录移动后重新注册服务器路径。更新不会主动清空 `state/`。

卸载前查看当前 CLI：`codex plugin remove --help`、`codex mcp remove --help`。通常可以运行 `codex plugin remove execution-panel@codex-execution-panel` 和 `codex mcp remove execution-panel`。这些操作不替你删除本地 SQLite；是否保留任务历史由你决定。

## 依据

本仓库使用仍被支持的 `.codex-plugin/plugin.json` 兼容清单及 repo marketplace，参见 [OpenAI 官方打包文档](https://developers.openai.com/plugins/build/plugins)。UI 使用 [MCP Apps](https://developers.openai.com/plugins/build/app-quickstart)；本地发布源码不等于上架官方目录。
