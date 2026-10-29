import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../assets/panel-theme.js', import.meta.url), 'utf8');

function harness({ dark = false, host, search = '' } = {}) {
  const root = { dataset: {} };
  const mediaListeners = new Set();
  const windowListeners = new Map();
  const media = {
    matches: dark,
    addEventListener: (name, listener) => mediaListeners.add(listener),
    removeEventListener: (name, listener) => mediaListeners.delete(listener),
  };
  const window = {
    location: { search },
    openai: host ? { theme: host } : undefined,
    matchMedia(query) { assert.equal(query, '(prefers-color-scheme: dark)'); return media; },
    addEventListener(name, listener) { windowListeners.set(name, listener); },
    removeEventListener(name, listener) { if (windowListeners.get(name) === listener) windowListeners.delete(name); },
  };
  vm.runInNewContext(source, { window, document: { documentElement: root }, URLSearchParams });
  return {
    root, theme: window.executionPanelTheme,
    system(dark) { media.matches = dark; for (const listener of mediaListeners) listener(); },
    close() { windowListeners.get('pagehide')?.(); },
    listenerCount: () => mediaListeners.size,
  };
}

test('standalone theme follows the OS initially and on changes', () => {
  const h = harness();
  assert.equal(h.root.dataset.theme, 'light');
  h.system(true);
  assert.equal(h.root.dataset.theme, 'dark');
  h.system(false);
  assert.equal(h.root.dataset.theme, 'light');
  h.close();
});

test('client theme overrides a different OS preference and tracks client changes', () => {
  const h = harness({ dark: true, host: 'light' });
  assert.equal(h.root.dataset.theme, 'light');
  h.system(true);
  assert.equal(h.root.dataset.theme, 'light');
  h.theme.setHostTheme('dark');
  assert.equal(h.root.dataset.theme, 'dark');
  h.system(false);
  assert.equal(h.root.dataset.theme, 'dark');
  h.theme.setHostTheme('light');
  assert.equal(h.root.dataset.theme, 'light');
  h.close();
});

test('partial or invalid host context does not discard a confirmed client theme', () => {
  const h = harness({ dark: false });
  h.theme.setHostTheme('dark');
  for (const theme of [undefined, null, {}, 'sepia', false]) h.theme.setHostTheme(theme);
  assert.equal(h.root.dataset.theme, 'dark');
  h.close();
});

test('returning the client to system preference restores live OS following', () => {
  const h = harness({ dark: true, host: 'light' });
  h.theme.setHostTheme('system');
  assert.equal(h.root.dataset.theme, 'dark');
  h.system(false);
  assert.equal(h.root.dataset.theme, 'light');
  h.close();
});

test('comparison overrides are restricted to explicit local preview mode', () => {
  const native = harness({ host: 'dark', search: '?theme=light' });
  assert.equal(native.root.dataset.theme, 'dark');
  const preview = harness({ dark: false, search: '?preview=1&theme=dark' });
  assert.equal(preview.root.dataset.theme, 'dark');
  preview.theme.setHostTheme('light');
  assert.equal(preview.root.dataset.theme, 'dark');
  const invalid = harness({ dark: true, search: '?preview=1&theme=sepia' });
  assert.equal(invalid.root.dataset.theme, 'dark');
  native.close(); preview.close(); invalid.close();
});

test('teardown removes the OS listener and prevents further page updates', () => {
  const h = harness();
  assert.equal(h.listenerCount(), 1);
  h.close();
  assert.equal(h.listenerCount(), 0);
  h.system(true);
  h.theme.setHostTheme('dark');
  assert.equal(h.root.dataset.theme, 'light');
});
