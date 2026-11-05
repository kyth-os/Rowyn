import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-clipboard-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynClipboard.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

function fakeDocument(result) {
  const appended = [];
  return {
    appended,
    document: {
      createElement: () => ({ value: '', style: {}, setAttribute() {}, select() { this.selected = true; } }),
      body: { appendChild: (node) => appended.push(node), removeChild: (node) => appended.splice(appended.indexOf(node), 1) },
      execCommand: () => result,
    },
  };
}

test('empty text is refused before touching the clipboard', async () => {
  const calls = [];
  const result = await m.copyText('', { clipboard: { writeText: async (value) => calls.push(value) } });
  assert.deepEqual(result, { ok: false, reason: 'There is nothing to copy yet.' });
  assert.deepEqual(calls, []);
});

test('the async clipboard is preferred when it works', async () => {
  const calls = [];
  const { document } = fakeDocument(true);
  const result = await m.copyText('report', { clipboard: { writeText: async (value) => calls.push(value) }, document });
  assert.deepEqual(result, { ok: true, method: 'clipboard' });
  assert.deepEqual(calls, ['report']);
});

test('a denied clipboard falls back to a selection copy', async () => {
  const { document, appended } = fakeDocument(true);
  const result = await m.copyText('report', {
    clipboard: { writeText: async () => { throw new Error('denied'); } },
    document,
  });
  assert.deepEqual(result, { ok: true, method: 'selection' });
  assert.equal(appended.length, 0, 'the temporary textarea must be removed');
});

test('a selection copy that fails explains the native alternative', async () => {
  const { document } = fakeDocument(false);
  const result = await m.copyText('report', { clipboard: null, document });
  assert.equal(result.ok, false);
  assert.match(result.reason, /Save report to Downloads/);
});

test('no document and no clipboard textarea exists yields a typed failure', async () => {
  const result = await m.copyText('report', { clipboard: null, document: null });
  assert.equal(result.ok, false);
  assert.match(result.reason, /Save report to Downloads/);
});
