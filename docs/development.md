# 开发与验证

运行只需要 Python 3.10+，前端是原生静态文件。没有 `pip install`、`npm install` 或打包构建步骤。

```sh
python -B -m unittest discover -s tests -q
node --test tests/test_bridge.mjs tests/test_theme.mjs tests/test_plan_model.mjs tests/test_panel_ui.mjs
```

测试只使用合成任务，临时文件位于 Python 标准临时目录下；通过 `TEMP` / `TMP` / `TMPDIR` 选择位置。不要指向真实聊天数据。Windows 本机若需要所有临时文件留在 D:，可以先设置：

```powershell
New-Item -ItemType Directory -Force D:/Temp/codex-execution-panel | Out-Null
$env:TEMP = 'D:/Temp/codex-execution-panel'
$env:TMP = $env:TEMP
```

## 独立演示

```sh
python -B scripts/demo.py --scenario running --port 8767
```

终端会输出精确 URL。`running` 显示两个满足依赖的实际演示执行节点；`verification` 显示普通待验收；`blocker` 显示真实阻塞示例。

```sh
python -B scripts/demo.py --scenario verification --port 8767
python -B scripts/demo.py --scenario blocker --port 8767
```

每次运行均使用自动清理的独立临时目录、虚构日志和 Plan；不读取用户真实 Codex home，不写实际任务。没有定时推进百分比。Ctrl+C 结束。仅此明确开发命令启动 HTTP；正常原生面板不需要 HTTP 服务。预览的 @ 只复制，不提供原生草稿能力。

加 `&theme=dark` 或 `&theme=light` 可对照配色（仅 preview 模式接受主题覆盖）。文档截图来自该实际页面，需注明为合成演示；真实右栏截图应单独注明宿主。

## 手动验收边界

原生 UI 的自动展开和草稿导入不能仅用 fake host 测试证明。改动桥接或 @ 后，在真实客户端检查已有文字和附件保留、当前聊天不变、消息未发送、连击/轮询不重复、重开不自动导入、确认失败不自动重试。

新增状态要同时核对徽标、卡点数、展开规则、动效、并行统计和完成保护。`verification` 不等于 `needsReview`。

## 目录

`server.py` 提供 stdio、明确调用的本地预览和状态维护 CLI；`panel/` 是协议、事务状态和有界日志读取；`assets/` 是同一套原生/预览界面；`skills/` 提供 Agent 协作规则；`tests/` 覆盖协议和状态边界。参见 [architecture.md](architecture.md)。
