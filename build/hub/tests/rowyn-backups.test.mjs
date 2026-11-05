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
test('page renders all Kup facts, settings action and safety boundaries', async () => {
 const page = await readFile(resolve(root,'src/pages/Backups.tsx'),'utf8');
 for (const text of ['hub-section-backups','Rowyn Backups','included','excluded','lastCompleted','defaultIncluded','openRowynSettings','OS rollback does not restore personal files','same disk','Included:']) assert.ok(page.includes(text), text);
});
