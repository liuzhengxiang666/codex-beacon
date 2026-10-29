import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(new URL('../assets/plan-model.js', import.meta.url), 'utf8'), context);
const { nodeState, parallelCount, interventionText } = context.window.ExecutionPlanModel;

test('only actual in-progress nodes contribute to the parallel indicator', () => {
  assert.equal(parallelCount([{ id: 'a', status: 'pending' }, { id: 'b', status: 'completed' }]), 0);
  assert.equal(parallelCount([{ id: 'a', status: 'in_progress' }, { id: 'b', status: 'in_progress' }]), 2);
});
test('blockers, decisions and unfinished prerequisites do not imply active parallel work', () => {
  const nodes = [{ id: 'a', status: 'in_progress' },
    { id: 'b', status: 'in_progress', dependsOn: ['a'] },
    { id: 'c', status: 'in_progress', issues: [{ kind: 'blocker', status: 'open' }] },
    { id: 'd', status: 'in_progress', issues: [{ kind: 'decision', status: 'open' }] }];
  assert.equal(parallelCount(nodes), 1);
});
test('completed prerequisites and resolved issues permit reported parallel work', () => {
  assert.equal(parallelCount([{ id: 'a', status: 'completed' },
    { id: 'b', status: 'in_progress', dependsOn: ['a'] },
    { id: 'c', status: 'in_progress', dependsOn: ['a'], issues: [{ kind: 'blocker', status: 'resolved' }] }]), 2);
});

test('waiting verification is neutral, inactive and distinct from changed requirements', () => {
  const waiting = { id: 'verify', status: 'in_progress', issues: [{ kind: 'verification', status: 'open' }] };
  assert.equal(nodeState(waiting).label, '待验收');
  assert.equal(nodeState(waiting).attention, 'verification');
  assert.equal(nodeState(waiting).waitingVerification, true);
  assert.equal(nodeState(waiting).active, false);
  assert.equal(parallelCount([waiting, { id: 'work', status: 'in_progress' }]), 1);
  assert.equal(nodeState({ ...waiting, needsReview: true }).label, '待修订');
  assert.equal(nodeState({ ...waiting, issues: [{ kind: 'verification', status: 'resolved' }] }).active, true);
});

test('real blockers and decisions take priority over verification and review', () => {
  const node = { status: 'in_progress', needsReview: true, issues: [{ kind: 'verification', status: 'open' }, { kind: 'decision', status: 'open' }] };
  assert.equal(nodeState(node).label, '待决定');
  node.issues.push({ kind: 'blocker', status: 'open' });
  assert.equal(nodeState(node).label, '有卡点');
  assert.equal(nodeState(node).attention, 'blocker');
  assert.equal(nodeState(node).waitingVerification, false);
});
test('intervention text maps current ordinal and includes the exact task, version and issue', () => {
  const plan = { sessionId: 'exact-task', planVersion: 12, nodes: [{ id: 'old' },
    { id: 'build', title: '实现', issues: [{ id: 'api', title: '缺少接口', detail: '等待返回结构', status: 'open' }] }] };
  const result = interventionText(plan, 'build', 'api');
  for (const value of ['exact-task', 'Plan v12', '第 02 项', '节点 ID：build', '问题 ID：api', '等待返回结构', '我的干预要求：']) assert.ok(result.includes(value));
  plan.nodes[1].issues[0].status = 'resolved';
  assert.throws(() => interventionText(plan, 'build', 'api'), /已更新/);
});

test('verification intervention names the pending acceptance without calling it a blocker', () => {
  const plan = { sessionId: 'exact-task', planVersion: 13, nodes: [{ id: 'verify', title: '验收', issues: [{ id: 'draft', title: '草稿导入', kind: 'verification', status: 'open' }] }] };
  const result = interventionText(plan, 'verify', 'draft');
  assert.ok(result.includes('待验收：草稿导入'));
  assert.ok(!result.includes('卡点：'));
  assert.throws(() => interventionText(plan, 'verify', 'missing'), /事项已更新/);
});

test('any node can be referenced with its current ordinal, status and requirements', () => {
  for (const [status, label] of Object.entries({ pending: '待开始', in_progress: '进行中', completed: '已完成', blocked: '受阻', cancelled: '已取消' })) {
    const plan = { sessionId: 'exact-task', planVersion: 15, nodes: [{ id: 'first' },
      { id: 'work', title: '实现', status, summary: '当前说明', acceptance: '保留要求', issues: [] }] };
    const result = interventionText(plan, 'work');
    for (const value of ['exact-task', 'Plan v15', '第 02 项', '节点 ID：work', `节点状态：${label}`, '节点说明：当前说明', '验收要求：保留要求']) assert.ok(result.includes(value));
    assert.ok(!result.includes('问题 ID：'));
    assert.ok(!result.includes('卡点：'));
    assert.throws(() => interventionText(plan, 'removed'), /节点已更新/);
  }
});
