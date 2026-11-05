import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-update-status-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynUpdateStatusModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const { projectUpdateBanner } = await import(pathToFileURL(out).href);
const fixture = (status = {}, extra = {}) => ({ snapshot: null, pending: null, health: null, status: { staged: false, rollback: false, check_state: 'idle', detail: '', ...status }, ...extra });

test('unavailable and failed reads never imply healthy/current', () => {
  assert.equal(projectUpdateBanner(null).state, 'unavailable');
  assert.equal(projectUpdateBanner(null, true).state, 'failed');
  assert.notEqual(projectUpdateBanner(null).state, 'current');
});

test('staged state takes precedence over stale available state', () => {
  const projected = projectUpdateBanner(fixture({ staged: true, check_state: 'available', remote_digest: 'sha256:remote' }));
  assert.equal(projected.state, 'restartready');
  assert.match(projected.guidance, /restart/i);
});

test('zero-count pending summaries and cached digests never imply an available system update', () => {
  assert.equal(projectUpdateBanner(fixture({check_state:'current',remote_digest:'sha256:'+'a'.repeat(64)}, {pending:{bootc:'current',flatpak:'0',firmware:'0'}})).state, 'current');
  assert.equal(projectUpdateBanner(fixture({check_state:'idle'}, {pending:{bootc:'unknown',flatpak:'0',firmware:'unknown'}})).state, 'unavailable');
  assert.equal(projectUpdateBanner(fixture({check_state:'idle'}, {pending:{bootc:'staged',flatpak:'0',firmware:'0'}})).state, 'restartready');
});

test('busy, blocked, failed, available and current statuses project explicitly', () => {
  assert.equal(projectUpdateBanner(fixture({ check_state: 'busy' })).state, 'staging');
  assert.equal(projectUpdateBanner(fixture({ check_state: 'blocked', blocked_reason: 'Safety hold' })).state, 'blocked');
  assert.match(projectUpdateBanner(fixture({ check_state: 'blocked', blocked_reason: 'Safety hold' })).guidance, /Safety hold/);
  assert.equal(projectUpdateBanner(fixture({ check_state: 'error' })).state, 'failed');
  assert.equal(projectUpdateBanner(fixture({ check_state: 'available' })).state, 'available');
  assert.equal(projectUpdateBanner(fixture({ check_state: 'uptodate' })).state, 'current');
});
