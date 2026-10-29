# 发布宣传素材

## 中文短帖

我给 Codex 做了一个开源执行侧栏：**Codex Execution Panel**。

起因很简单：长任务跑起来后，我不想再隔几分钟问一次“你做到哪了”。

它把 Plan 节点和总体进度放到右侧，卡点、待决定、待验收分别标注。想调整某一步，点节点旁的 @，上下文就进入当前输入框，补充要求后再发送。正文继续输出，不用翻长对话重新解释。

目前是 Preview，Windows 原生客户端已实测，进度由 Agent 上报；其他平台的适配和英文界面欢迎一起完善。

源码：https://github.com/liuzhengxiang666/codex-execution-panel

欢迎试用、提问题，也欢迎贡献你觉得真正有用的改进。

## English post

I built an open-source execution sidebar for Codex because I kept asking: “What step are we on?”

Codex Execution Panel shows major plan steps, overall progress, blockers and pending verification beside the conversation. Click @ on any step to bring its context into your current draft, then add your instructions and send when ready.

It's a local MCP app, with agent-reported progress and no automatic message sending. This is an early preview, manually verified on a Windows desktop host. Compatibility reports and contributions are welcome.

https://github.com/liuzhengxiang666/codex-execution-panel

## 录屏建议

录制实际界面：开始多步任务 → 右栏出现 → 节点更新 → @ 一个普通节点 → 草稿出现，停在未发送状态。演示任务须使用合成内容，避免录入账号、私人对话与路径。不要用时间驱动的假百分比冒充任务进度。
