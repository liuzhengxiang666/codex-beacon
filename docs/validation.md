# Preview 验证记录

日期：2026-10-29。

## Codex Beacon v0.5.3 本次发布

- 91 项 Python、29 项 Node 检查通过。新增实际 hook 命令执行验证，覆盖含空格和中文的插件路径；同时检查偏好读写、首次明确选择、已有规则保留、畸形配置拒绝和不提升任意 AGENTS 文本。
- 在 D: 的隔离 `CODEX_HOME` 实际执行 Windows 注册脚本、`mcp get`、marketplace 添加、0.5.3 安装和列表查询，确认 enabled=true；auto 偏好保存后读回一致。不改现用客户端配置或信任状态。CLI 在临时 home 中提示跳过 PATH helper aliases，注册及安装命令均成功。
- 修改后的真实演示页面：深色 420px、浅色 360px，scrollWidth 等于视口宽度；Beacon 品牌可见、旧底部提示不存在。截图为 `beacon-dark.jpg` 和 `beacon-light.jpg`，使用公开的隔离合成任务，不含用户会话。
- 运行时协议版本为 0.5.3；MCP 名称、marketplace/技能 ID、资源 URI 和状态格式保持原值。
- 本次没有把 hooks 单元测试或演示页面当成原生自动展开验收。v0.5.3 hooks 在无 @ 新任务中必定展开右栏，仍待真实原生端到端验证。历史原生工具调用后展开与 @ 草稿证据保持原有范围。

下文为 v0.5.1 的历史验证记录；GitHub Actions 另记录本次提交的跨平台自动检查结果。

## 本机环境

- Windows；桌面包 `OpenAI.Codex 26.1023.6123.0`。
- `codex-cli 0.158.0`。
- Python `3.14.5`，Node `24.16.0`。

## 已验证

- 从不含本地会话数据的发布目录运行：80 项 Python、29 项 Node 测试通过。
- 隔离 `CODEX_HOME` 中实跑注册脚本、`mcp get`、本地 marketplace 添加、技能安装和列表核对：安装启用 `execution-panel@codex-execution-panel`，不改现用配置。
- 实际 Windows 原生右栏：自动展开、主要节点更新、主题和单列布局；普通已完成节点 @ 导入当前草稿。此前验收覆盖原文保留、不发送、不新建聊天、确认跨刷新保持、重开不自动导入；本轮普通节点测试内容已清理。
- 发布截图：`native-sidebar.png` 为上述真实原生右栏；`demo-*.jpg` 为同一实际 UI 在 420px 窄宽下运行合成任务的截图，分别覆盖运行并行、浅色中性待验收、深色卡点。
- 演示仅创建虚构 rollout 和独立临时状态，不接触真实 Codex home。

自动测试包括完成保护、依赖与循环拒绝、历史版本、并发写入、协议/身份检查、实际 stdio 进程、预览本机边界、主题、草稿防重复和确认失败不重试。

## 不应从上述结论推断

不能据此声称所有客户端会自动打开右栏，所有宿主支持 @ 草稿，macOS/Linux 原生交互已通过，或面板能订阅/控制 Goal 的真实生命周期。CI 的 Python/OS 矩阵测试也不等于这些平台的桌面 UI 验收。

仓库的 GitHub Actions 记录是每次提交的自动检查结果来源；本文件记录本机与原生 UI 验收范围。
