import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-section-')), 'model.mjs');
// react is bundled, not external: the bundle is imported from a temp directory
// that has no node_modules of its own.
await build({ entryPoints: [resolve(root, 'src/hooks/useSectionFollow.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

const MAP = { Gaming: 'hub-section-gaming', Controllers: 'hub-section-controllers' };

test('only declared sections resolve to anchors', () => {
  assert.equal(m.sectionAnchor('Gaming', MAP), 'hub-section-gaming');
  assert.equal(m.sectionAnchor('Unknown', MAP), null);
  assert.equal(m.sectionAnchor('', MAP), null);
  assert.equal(m.sectionAnchor(null, MAP), null);
  assert.equal(m.sectionAnchor('toString', MAP), null, 'prototype keys are not sections');
});

test('the declared section is read from the hash query or the search string', () => {
  globalThis.window = { location: { hash: '#/play?section=Controllers', search: '' } };
  assert.equal(m.currentSection(), 'Controllers');
  globalThis.window = { location: { hash: '#/play', search: '?section=Gaming' } };
  assert.equal(m.currentSection(), 'Gaming');
  globalThis.window = { location: { hash: '#/play', search: '' } };
  assert.equal(m.currentSection(), null);
  delete globalThis.window;
});
