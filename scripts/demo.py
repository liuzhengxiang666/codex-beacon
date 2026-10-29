"""Run the real viewer against isolated synthetic state; never read user logs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panel.protocol import PanelAPI
from panel.state import PanelService
from server import preview

SESSION = "11111111-1111-4111-8111-111111111111"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("running", "verification", "blocker"), default="running")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="execution-panel-demo-") as temporary:
        base = Path(temporary)
        sessions = base / "codex/sessions"
        sessions.mkdir(parents=True)
        events = [
            {"type": "session_meta", "payload": {"id": SESSION}},
            {"type": "event_msg", "timestamp": "2026-10-29T04:00:00Z", "payload": {"type": "task_started"}},
        ]
        (sessions / f"rollout-demo-{SESSION}.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        plan = json.loads((ROOT / "examples/demo-plan.json").read_text(encoding="utf-8"))
        if args.scenario == "verification":
            for node in plan["nodes"][:3]:
                node["status"] = "completed"
            plan["nodes"][3]["status"] = "in_progress"
        elif args.scenario == "blocker":
            plan["nodes"][1]["status"] = "blocked"
            plan["nodes"][2]["status"] = "pending"
        service = PanelService(base / "state", base / "codex")
        service.set_plan(SESSION, plan["title"], plan["nodes"])
        if args.scenario == "verification":
            service.update_node(SESSION, "acceptance", summary="实现和自动检查已完成，等待确认实际界面。", issue={
                "id": "visual-acceptance", "kind": "verification", "status": "open",
                "title": "确认窄窗口下的筛选体验", "detail": "请检查按钮是否易于操作，验收后记录依据。"})
        elif args.scenario == "blocker":
            service.update_node(SESSION, "interface", summary="发现存储契约缺少必要字段。", issue={
                "id": "storage-contract", "kind": "blocker", "status": "open",
                "title": "缺少任务唯一标识", "detail": "恢复接口未返回稳定 ID，继续保存可能覆盖已有任务。"})
        print(f"Synthetic demo ({args.scenario}): http://127.0.0.1:{args.port}/?preview=1&sessionId={SESSION}", flush=True)
        try:
            preview(PanelAPI(service, ROOT), args.port)
        except KeyboardInterrupt:
            print("\nDemo stopped; isolated temporary state cleaned up.")


if __name__ == "__main__":
    main()
