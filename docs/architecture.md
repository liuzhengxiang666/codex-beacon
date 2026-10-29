# 架构与工具

数据路径：**Agent / 结构化本地日志 → Python stdio MCP → 本地 SQLite / 状态快照 → MCP App 右栏 → 显式 @ → 当前聊天草稿**。

## 复用关系

- `server.py`：同一个 `PanelAPI` 供 stdio、本地 preview 与 CLI 使用；不引入另一套服务端状态。
- `panel/protocol.py`：工具定义、调用校验、精确聊天绑定与资源元数据；加载 UI 时把本地 CSS/JS 内联到 MCP App 资源。
- `panel/state.py`：SQLite 事务、稳定 ID、计划版本、历史归档、依赖与完成保护。
- `panel/logs.py`：有界读取 `CODEX_HOME/sessions` 及 `archived_sessions` 中可识别的 rollout 文件；不修改原始日志。
- `panel/interactions.py`：保留早期请求及送达记录兼容。当前 UI 不主动提交旧式反馈；记录状态不代表 Goal 已停止。
- `assets/plan-model.js`：状态优先级、有效并行计数与节点/事项提示词。
- `assets/panel.js`：串行只读刷新、交互保留、草稿尝试去重；隐藏页面暂停刷新，失败退避。
- `assets/mcp-app-bridge.js`：宿主握手、能力检查和 `active + send=false` 草稿桥接，保留第三方归属。

## Agent 主要工具

| 工具 | 职责 |
| --- | --- |
| `read_execution_plan` | 读取当前计划、版本、历史与完成依据 |
| `set_execution_plan` | 初始化/调整主要节点；修改带 `reason` 和 `expectedVersion` |
| `report_execution_update` | 上报节点状态、摘要、事项及完成依据 |
| `show_execution_panel` | 请求宿主展示当前精确聊天的原生面板 |
| `resolve_execution_request` | 记录遗留显式请求的处理结果，不直接控制 Goal |

`get_execution_panel_update` 等 app-only 工具用于视图刷新，不应变成模型每秒轮询。定义及准确字段以 `panel/protocol.py` 为准。

节点状态：`pending`、`in_progress`、`completed`、`blocked`、`cancelled`。事项类型：`blocker`、`decision`、`branch`、`verification`，使用原 ID 解决。未解决的 blocker / decision / verification 阻止完成；branch 是路线记录。`needsReview` 表示要求改变后的待修订。

缺少宿主身份时要显式传入精确 `sessionId`，并验证本地存在对应任务。禁止猜最近聊天或依赖用户看不到的任务切换。

## 不负责的事情

不管理 Agent 调度、原生 Goal 生命周期、模型用量或终端进程；不执行任意服务器命令；不从自然语言解释中伪造节点完成。面板可视化与宿主执行控制的状态来源不同。
