import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

// Exercise the shipped viewer, not a reimplementation of its render/export logic.
// Only the small DOM surface used by the viewer and the host boundary are faked.
const viewer = fs.readFileSync(new URL('../assets/panel.js', import.meta.url), 'utf8');
const model = fs.readFileSync(new URL('../assets/plan-model.js', import.meta.url), 'utf8');

function acceptancePlan(overrides = {}) {
  return {
    schemaVersion: 1, sessionId: 'synthetic-ui-task', planVersion: 13, revision: 'r1',
    title: 'Synthetic acceptance', runtime: { status: 'running' },
    viewerBinding: { verified: true, sessionId: 'synthetic-ui-task' },
    nodes: [{ id: 'verify', title: '草稿验收', status: 'in_progress', summary: '等待真实验收',
      issues: [{ id: 'draft', title: '导入草稿', kind: 'verification', status: 'open' }] }],
    progress: { total: 1, completed: 0, percent: 0 },
    requests: [], history: [], archivedNodes: [], planChanges: [], ...overrides,
  };
}

function harness({ initial = acceptancePlan(), draft = async () => {}, supportsDraft = true } = {}) {
  let document;
  class Element {
    constructor(tag) {
      this.tagName = tag.toLowerCase(); this.children = []; this.parentNode = null;
      this.dataset = {}; this.style = {}; this.attributes = new Map(); this.events = new Map();
      this.hidden = false; this.open = false; this.disabled = false; this.className = ''; this.ownText = '';
    }
    get childNodes() { return this.children; }
    get open() { return this._open; }
    set open(value) { this._open = Boolean(value); }
    get options() { return this.children.filter(child => child.tagName === 'option'); }
    get textContent() { return this.ownText + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this.ownText = String(value); this.children = []; }
    append(...nodes) {
      for (const node of nodes) {
        if (node.tagName === '#fragment') { this.append(...node.children); continue; }
        node.parentNode = this; this.children.push(node);
      }
    }
    replaceChildren(...nodes) { this.children = []; this.ownText = ''; this.append(...nodes); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    removeAttribute(name) { this.attributes.delete(name); }
    addEventListener(name, fn) {
      if (!this.events.has(name)) this.events.set(name, []);
      this.events.get(name).push(fn);
    }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    focus() { document.activeElement = this; }
    querySelectorAll(selector) {
      const matches = node => selector === 'details[data-key]' ? node.tagName === 'details' && node.dataset.key !== undefined :
        selector === '[data-focus-key]' ? node.dataset.focusKey !== undefined : false;
      return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    async click() {
      if (this.disabled) return;
      const results = (this.events.get('click') || []).map(fn => fn({ target: this }));
      if (this.tagName === 'summary' && this.parentNode?.tagName === 'details') this.parentNode.open = !this.parentNode.open;
      await Promise.all(results);
    }
  }
  const ids = new Map([...viewer.matchAll(/byId\("([^"]+)"\)/g)].map(([, id]) => [id, new Element('div')]));
  const timers = new Map(); let nextTimer = 0; let callbacks; let current = initial;
  const exports = []; const copies = []; const reads = [];
  document = {
    hidden: false, activeElement: null, documentElement: {},
    getElementById: id => ids.get(id), createElement: tag => new Element(tag),
    createDocumentFragment: () => new Element('#fragment'), addEventListener() {},
  };
  const host = {
    supportsDraft: () => supportsDraft,
    async start() { callbacks.onResult({ structuredContent: current }); },
    async appendDraft(content) { exports.push(content); return draft(content); },
    async callTool(name) { reads.push(name); return { structuredContent: current }; },
  };
  const window = {
    location: { search: '' }, scrollX: 0, scrollY: 0,
    setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); }, scrollTo() {}, addEventListener() {},
    createTrajectoryHost(value) { callbacks = value; return host; },
  };
  const context = vm.createContext({ window, document, URL, URLSearchParams,
    navigator: { clipboard: { async writeText(content) { copies.push(content); } } } });
  vm.runInContext(model, context, { filename: 'plan-model.js' });
  vm.runInContext(viewer, context, { filename: 'panel.js' });
  const all = (root, predicate) => [...(predicate(root) ? [root] : []), ...root.children.flatMap(child => all(child, predicate))];
  const byClass = (name, root = ids.get('route')) => all(root, node => node.className.split(' ').includes(name));
  return {
    ids, exports, copies, reads, byClass,
    row: () => ids.get('route').children[0],
    details: () => byClass('node-details')[0],
    button: () => byClass('intervention-button')[0],
    push(next) { current = next; callbacks.onResult({ structuredContent: current }); },
    contextUpdate() { callbacks.onContext({ theme: 'dark' }); },
    async poll() {
      const timer = timers.entries().next().value;
      assert.ok(timer, 'viewer scheduled an independent refresh');
      timers.delete(timer[0]); timer[1]();
      for (let index = 0; index < 6; index++) await Promise.resolve();
    },
  };
}

test('pending verification is neutral and does not advertise a blocker or active work', () => {
  const h = harness();
  assert.equal(h.row().dataset.attention, 'verification');
  assert.equal(h.byClass('node-state')[0].textContent, '待验收');
  assert.equal(h.details().open, false);
  assert.equal(h.ids.get('execution-panel').dataset.executing, 'false');
  assert.equal(h.ids.get('parallel-state').hidden, true);
  assert.match(h.ids.get('status-announcement').textContent, /1 项待验收/);
  assert.doesNotMatch(h.ids.get('status-announcement').textContent, /卡点/);
  assert.equal(h.byClass('issue--blocker').length, 0);
  assert.equal(h.byClass('issue--verification').length, 1);
});

test('manual expansion of a verification survives polling and host-context rerenders', async () => {
  const h = harness();
  await h.byClass('node-summary')[0].click();
  assert.equal(h.details().open, true);
  h.push(acceptancePlan({ revision: 'r2', nodes: [{ ...acceptancePlan().nodes[0], summary: '验收进度已更新' }] }));
  assert.equal(h.details().open, true);
  h.contextUpdate();
  assert.equal(h.details().open, true);
  await h.poll();
  assert.equal(h.details().open, true);
  assert.equal(h.exports.length, 0);
});

test('a real blocker remains prominent when verification is also open', () => {
  const initial = acceptancePlan();
  initial.nodes[0].issues.push({ id: 'api', kind: 'blocker', status: 'open', title: '接口不可用' });
  const h = harness({ initial });
  assert.equal(h.row().dataset.attention, 'blocker');
  assert.equal(h.byClass('node-state')[0].textContent, '有卡点');
  assert.equal(h.details().open, true);
  assert.match(h.ids.get('status-announcement').textContent, /1 处卡点/);
});

test('double click and polling never duplicate a pending draft export; confirmation survives rerender', async () => {
  let confirm;
  const pending = new Promise(resolve => { confirm = resolve; });
  const h = harness({ draft: () => pending });
  const first = h.button().click();
  await h.button().click();
  await h.poll();
  assert.equal(h.exports.length, 1);
  assert.match(h.exports[0], /待验收：导入草稿/);
  assert.match(h.exports[0], /synthetic-ui-task/);
  assert.equal(h.button().disabled, true);
  confirm({}); await first;
  assert.equal(h.button().textContent, '已加入草稿');
  assert.equal(h.button().disabled, true);
  h.push(acceptancePlan({ revision: 'r2', nodes: [{ ...acceptancePlan().nodes[0], summary: '节点说明已更新' }] }));
  h.contextUpdate(); await h.poll(); await h.button().click();
  assert.equal(h.button().textContent, '已加入草稿');
  assert.equal(h.button().disabled, true);
  assert.match(h.byClass('intervention-feedback')[0].textContent, /已加入当前草稿/);
  assert.equal(h.exports.length, 1);
  assert.equal(h.copies.length, 0);
});

test('unconfirmed draft export is not retried by polling or another click; the explicit fallback only copies', async () => {
  const h = harness({ draft: async () => { throw new Error('missing acknowledgement'); } });
  await h.button().click();
  assert.equal(h.exports.length, 1);
  assert.equal(h.button().textContent, '@ 复制干预提示');
  assert.match(h.byClass('intervention-feedback')[0].textContent, /未确认/);
  h.contextUpdate(); await h.poll();
  assert.equal(h.exports.length, 1);
  await h.button().click();
  assert.equal(h.exports.length, 1);
  assert.equal(h.copies.length, 1);
  assert.equal(h.copies[0], h.exports[0]);
});

test('a reopened viewer never exports a draft until the user explicitly clicks', async () => {
  const first = harness();
  await first.button().click();
  assert.equal(first.exports.length, 1);
  const reopened = harness();
  reopened.contextUpdate(); await reopened.poll();
  assert.equal(reopened.exports.length, 0);
  assert.equal(reopened.copies.length, 0);
  await reopened.button().click();
  assert.equal(reopened.exports.length, 1);
});

test('all node statuses expose a node entry outside collapsed details and export normal context', async () => {
  for (const status of ['pending', 'in_progress', 'completed', 'blocked', 'cancelled']) {
    const initial = acceptancePlan({ nodes: [{ id: 'ordinary', title: '普通节点', status, summary: '调整这里', acceptance: '保留原要求', issues: [] }] });
    const h = harness({ initial });
    assert.equal(h.byClass('intervention--node').length, 1);
    assert.equal(h.button().parentNode.parentNode, h.row());
    const expanded = h.details().open;
    assert.equal(h.button().textContent, '@');
    const first = h.button().click();
    await h.button().click(); await first;
    assert.equal(h.exports.length, 1);
    assert.match(h.exports[0], /节点 ID：ordinary/);
    assert.match(h.exports[0], /验收要求：保留原要求/);
    assert.doesNotMatch(h.exports[0], /问题 ID：|卡点：/);
    assert.equal(h.details().open, expanded);
    assert.equal(h.button().textContent, '✓');
    await h.poll(); h.contextUpdate(); await h.button().click();
    assert.equal(h.exports.length, 1);
    assert.equal(h.button().disabled, true);
  }
});

test('node and branch entries keep separate context and draft confirmation', async () => {
  const initial = acceptancePlan(); initial.nodes[0].issues[0].kind = 'branch';
  const h = harness({ initial });
  assert.equal(h.byClass('intervention-button').length, 2);
  await h.byClass('intervention-button')[0].click();
  await h.byClass('intervention-button')[1].click();
  assert.equal(h.exports.length, 2);
  assert.match(h.exports[0], /分支：导入草稿/);
  assert.match(h.exports[0], /问题 ID：draft/);
  assert.doesNotMatch(h.exports[1], /问题 ID：/);
  assert.equal(h.byClass('intervention-button').every(button => button.disabled), true);
});

test('node entries fall back to copying when native draft support is unavailable', async () => {
  const initial = acceptancePlan({ nodes: [{ id: 'work', title: '普通节点', status: 'pending' }] });
  const h = harness({ initial, supportsDraft: false });
  await h.button().click();
  assert.equal(h.exports.length, 0);
  assert.equal(h.copies.length, 1);
  assert.match(h.copies[0], /节点 ID：work/);
});
