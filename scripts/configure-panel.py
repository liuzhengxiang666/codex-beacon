"""Configure the panel once in the existing user-level AGENTS.md, not a second DB."""
from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
from auto_panel import START, END, preference_path, read_preference

RULES = {
 'auto': '对实际需要多步骤的已授权任务默认开启原生右侧执行面板。包括检索、研究、跨聊天阅读、核对、比较、总结、排期，以及开发测试；不要按请求标题、是否写代码或是否调用update_plan排除。直接可答的简短问答、未展开调查的方案讨论和单步操作不自动开。',
 'ask': '实际多步任务开始时在主对话询问一次是否打开执行面板；未回答不视为同意。',
 'manual': '仅在用户明确要求时打开执行面板，不按多步任务自动打开。',
}


def configure(path, mode):
    path = Path(path)
    original = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    if original.count(START) != original.count(END) or original.count(START) > 1:
        raise ValueError('Malformed or duplicate preference block; preserve file for manual review.')
    block = f'{START}\nexecution-panel-mode: {mode}\n{RULES[mode]}\n使用精确当前聊天身份，复用同一Plan与已打开右栏；尊重本聊天关闭或“这次不开”，不新建聊天、Goal、服务或网页替代。设置为用户级偏好，不是宿主强制自动展开的保证。\n{END}'
    if START in original:
        before, tail = original.split(START, 1)
        _, after = tail.split(END, 1)
        updated = before + block + after
    else:
        lines = original.splitlines(keepends=True)
        original_for_update = ''.join(
            '- 执行面板默认方式以文末 execution-panel-preference 配置块为准；只在用户明确更改偏好时更新。\n'
            if line.startswith('- 用户已选择“多步执行任务始终开启”') else line
            for line in lines
        )
        updated = original_for_update.rstrip() + ('\n\n' if original_for_update.strip() else '') + block + '\n'
    if updated == original:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = Path(__file__).resolve().parents[1] / 'work/config-backups'
        backup.mkdir(parents=True, exist_ok=True)
        (backup / ('AGENTS-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.md')).write_bytes(path.read_bytes())
    path.write_text(updated, encoding='utf-8')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['auto', 'ask', 'manual'])
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()
    path = preference_path()
    current = read_preference(path)
    if args.show:
        print(current or 'not-configured')
        return
    if args.mode:
        mode = args.mode
    elif current:
        print('Keeping saved execution-panel mode: ' + current)
        return
    elif not sys.stdin.isatty():
        parser.error('First setup requires --mode auto|ask|manual or an interactive terminal.')
    else:
        print('Codex Beacon / 执行面板：1 多步任务自动开(推荐) / 2 每次询问 / 3 手动开')
        answer = input('Choose 1/2/3: ').strip()
        modes = {'1': 'auto', '2': 'ask', '3': 'manual'}
        if answer not in modes:
            parser.error('No setting saved; choose 1, 2 or 3.')
        mode = modes[answer]
    configure(path, mode)
    print('Saved execution-panel mode: ' + mode)


if __name__ == '__main__':
    main()
