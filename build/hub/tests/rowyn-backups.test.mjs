import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-backups-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynBackupModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);
test('backup model distinguishes status and summary', () => {
  assert.equal(m.backupSummary({state:'plans',detail:'',plans:[{},{}]}), '2 Kup backup plan(s) configured.');
  assert.equal(m.backupStatus({enabled:false}), 'Disabled');
});
test('backup age is explicit instead of leaving a bare date to interpret', () => {
  const now = Date.parse('2026-11-05T12:00:00Z');
  const at = days => new Date(now - days * 86_400_000).toISOString();
  assert.deepEqual(m.backupFreshness(null, now), { state:'unknown', days:null, label:'No completed backup recorded by Kup' });
  assert.equal(m.backupFreshness('not a date', now).state, 'unknown');
  assert.deepEqual(m.backupFreshness(at(0), now), { state:'fresh', days:0, label:'Completed today' });
  assert.equal(m.backupFreshness(at(3), now).state, 'fresh');
  assert.equal(m.backupFreshness(at(9), now).state, 'stale');
  assert.equal(m.backupFreshness(at(45), now).state, 'old');
  assert.match(m.backupFreshness(at(45), now).label, /45 days old/);
  assert.equal(m.backupFreshness(at(-2), now).days, 0, 'clock skew never yields a negative age');
});
test('page renders all Kup facts, settings action and safety boundaries', async () => {
 const page = await readFile(resolve(root,'src/pages/Backups.tsx'),'utf8');
 for (const text of ['hub-section-backups','Rowyn Backups','included','excluded','lastCompleted','defaultIncluded','openRowynSettings','OS rollback does not restore personal files','same disk','Included:','Restore files manually','Versioned backups','Mirror backups','Test restore one file','new, separate destination folder','never select the source folder','does not prove the destination is connected','never restores files','backupFreshness']) assert.ok(page.includes(text), text);
});
