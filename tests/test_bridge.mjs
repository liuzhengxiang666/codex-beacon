import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const source = fs.readFileSync(path.join(root, 'assets/mcp-app-bridge.js'), 'utf8');

function harness(capabilities = {}) {
  const listeners = new Map();
  const posted = [];
  const results = [];
  const contexts = [];
  const errors = [];
  let teardownCount = 0;
  const window = {
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
    },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
  };
  function emit(name, event) {
    for (const fn of [...(listeners.get(name) || [])]) fn(event);
  }
  const parent = {
    postMessage(message) {
      posted.push(message);
      if (message.method === 'ui/initialize') {
        queueMicrotask(() => emit('message', { source: parent, data: {
          jsonrpc: '2.0', id: message.id,
          result: { protocolVersion: '2026-01-26', hostCapabilities: capabilities, hostContext: { theme: 'dark', availableDisplayModes: ['inline', 'fullscreen'] } },
        } }));
      } else if (message.method === 'tools/call') {
        queueMicrotask(() => emit('message', { source: parent, data: {
          jsonrpc: '2.0', id: message.id,
          result: { structuredContent: { sessionId: message.params.arguments.sessionId, revision: 'r1' } },
        } }));
      } else if (message.method === 'ui/message') {
        queueMicrotask(() => emit('message', { source: parent, data: {
          jsonrpc: '2.0', id: message.id, result: {},
        } }));
      }
    },
  };
  window.parent = parent;
  const context = vm.createContext({ window, setTimeout, clearTimeout, queueMicrotask });
  vm.runInContext(source, context, { filename: 'mcp-app-bridge.js' });
  const host = window.createTrajectoryHost({
    onResult: result => results.push(result),
    onContext: value => contexts.push(value),
    onError: error => errors.push(error),
    onTeardown: () => teardownCount++,
  });
  return { host, parent, posted, results, contexts, errors, emit, teardownCount: () => teardownCount };
}

test('native handshake completes before app-only tools/call, with no model message', async () => {
  const h = harness();
  await h.host.start();
  const result = await h.host.callTool('get_execution_panel_update', { sessionId: 'synthetic-task' });
  assert.equal(result.structuredContent.sessionId, 'synthetic-task');
  assert.deepEqual(h.posted.filter(m => m.method).map(m => m.method), [
    'ui/initialize', 'ui/notifications/initialized', 'tools/call',
  ]);
  assert.equal(h.posted[0].params.protocolVersion, '2026-01-26');
  assert.deepEqual(Array.from(h.posted[0].params.appCapabilities.availableDisplayModes), ['fullscreen']);
  assert.equal(h.host.supportsDisplayMode('fullscreen'), true);
  assert.equal(h.contexts.at(-1).theme, 'dark');
  assert.equal(h.errors.length, 0);
  h.emit('pagehide', {});
});

test('untrusted message sources are ignored; parent notifications update only local view', async () => {
  const h = harness();
  await h.host.start();
  h.emit('message', { source: {}, data: { jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: { spoofed: true } } });
  assert.equal(h.results.length, 0);
  h.emit('message', { source: h.parent, data: { jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: { structuredContent: { revision: 'r2' } } } });
  assert.equal(h.results.length, 1);
  assert.equal(h.results[0].structuredContent.revision, 'r2');
  h.emit('message', { source: h.parent, data: { jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { locale: 'zh-CN' } } });
  assert.equal(h.contexts.at(-1).locale, 'zh-CN');
  h.emit('message', { source: h.parent, data: { jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { theme: 'light' } } });
  assert.equal(h.contexts.at(-1).theme, 'light');
  assert.equal(h.contexts.at(-1).locale, 'zh-CN');
  assert.equal(h.posted.filter(m => /turn\/(?:start|steer)|ui\/message/.test(m.method || '')).length, 0);
  h.emit('pagehide', {});
});

test('resource teardown replies once and closes future calls', async () => {
  const h = harness();
  await h.host.start();
  h.emit('message', { source: h.parent, data: { jsonrpc: '2.0', id: 'teardown-1', method: 'ui/resource-teardown', params: {} } });
  assert.equal(h.teardownCount(), 1);
  assert.equal(h.posted.at(-1).id, 'teardown-1');
  assert.equal(typeof h.posted.at(-1).result, 'object');
  await assert.rejects(h.host.callTool('get_execution_panel_update', { sessionId: 'synthetic-task' }), /closed/);
  h.emit('pagehide', {});
  assert.equal(h.teardownCount(), 1);
});

test('viewer stays passive with no secondary chat input or automatic message sending', async () => {
  const h = harness();
  await h.host.start();
  assert.equal(h.posted.filter(message => message.method === 'ui/message').length, 0);
  assert.equal(h.host.sendMessage, undefined);
  await h.host.callTool('get_execution_panel_update', { sessionId: 'synthetic-task' });
  assert.equal(h.posted.filter(message => message.method === 'ui/message').length, 0);
  assert.equal(h.posted.filter(message => /turn\/|thread\/goal/.test(message.method || '')).length, 0);
  h.emit('pagehide', {});
});

test('draft export requires both text messaging and the explicit draft extension', async () => {
  for (const capabilities of [{}, { message: { text: {} } }, { experimental: { 'openai/message': {} } }]) {
    const h = harness(capabilities);
    await h.host.start();
    assert.equal(h.host.supportsDraft(), false);
    await assert.rejects(h.host.appendDraft('intervention'), /不支持/);
    assert.equal(h.posted.filter(message => message.method === 'ui/message').length, 0);
    h.emit('pagehide', {});
  }
});

test('explicit draft export always appends to active draft without sending or a new chat', async () => {
  const h = harness({ message: { text: {} }, experimental: { 'openai/message': {} } });
  await h.host.start();
  assert.equal(h.host.supportsDraft(), true);
  assert.equal(h.posted.filter(message => message.method === 'ui/message').length, 0);
  await h.host.appendDraft('node and blocker context');
  const messages = h.posted.filter(message => message.method === 'ui/message');
  assert.equal(messages.length, 1);
  assert.equal(messages[0].params._meta['openai/message'].target, 'active');
  assert.equal(messages[0].params._meta['openai/message'].send, false);
  assert.equal(messages[0].params.content[0].text, 'node and blocker context');
  await assert.rejects(h.host.appendDraft(''), /无效/);
  h.emit('pagehide', {});
  await assert.rejects(h.host.appendDraft('after close'), /closed/);
});
