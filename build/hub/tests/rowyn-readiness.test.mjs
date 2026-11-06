import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const directory = await mkdtemp(resolve(tmpdir(), 'rowyn-readiness-'));
const outfile = resolve(directory, 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynReadinessModel.ts')], outfile,
  bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const model = await import(pathToFileURL(outfile).href);
const fixture = () => ({ checkedAt: Math.floor(Date.now() / 1000), checks: [
  { id: 'printing', title: 'Printing', state: 'attention', detail: 'Printer discovery needs attention.' },
  { id: 'logging', title: 'System logging', state: 'ready', detail: 'The system journal is running.' },
  { id: 'updates', title: 'Updates', state: 'needs-setup', detail: 'Automatic updates have not reported a check.' },
  { id: 'backups', title: 'Backups', state: 'needs-setup', detail: 'No backup plans are configured.' },
  { id: 'development', title: 'Development tools', state: 'unavailable', detail: 'Development prerequisites could not be checked.' },
] });

test('validates all native checks and preserves attention/setup/unavailable rather than claiming healthy', () => {
  const input = fixture();
  assert.deepEqual(model.validateReadiness(input), input);
  assert.deepEqual(Object.keys(model.readinessTargets).toSorted(), input.checks.map((check) => check.id).toSorted());
  assert.deepEqual(model.readinessTargets.printing, { label: 'Open Print settings', setting: 'printers' });
  assert.deepEqual(model.readinessTargets.logging, { label: 'Open Diagnostics', route: '/diagnostics' });
  assert.deepEqual(model.readinessTargets.updates, { label: 'Open Updates', route: '/updates' });
  assert.deepEqual(model.readinessTargets.backups, { label: 'Open Backups', route: '/backups' });
  assert.deepEqual(model.readinessTargets.development, { label: 'Open Development', route: '/development' });
});

test('rejects incomplete, duplicate, inherited, invalid or oversized native results', () => {
  const input = fixture();
  for (const bad of [
    { ...input, checks: [] }, { ...input, checks: input.checks.slice(1) },
    { ...input, checks: [...input.checks, input.checks[0]] },
    { ...input, checks: input.checks.map((check, index) => index ? check : { ...check, id: '__proto__' }) },
    { ...input, checks: input.checks.map((check, index) => index ? check : { ...check, state: 'healthy' }) },
    { ...input, checks: input.checks.map((check, index) => index ? check : { ...check, title: 'Anything else' }) },
    { ...input, checks: input.checks.map((check, index) => index ? check : { ...check, detail: 'x'.repeat(241) }) },
    { ...input, checks: input.checks.map((check, index) => index ? check : { ...check, detail: 'unsafe\u0000text' }) },
    { ...input, checkedAt: true }, { ...input, checkedAt: -1 },
    { ...input, checkedAt: input.checkedAt + 3600 },
  ]) assert.throws(() => model.validateReadiness(bad), /invalid/i);
});

test('preserves prior successful history even when its timestamp follows the new attempt start', () => {
  const input = fixture(); const now = input.checkedAt;
  const attempt = {startedAt:now-90,finishedAt:now-20,outcome:'failed',reasonCode:'runtime_auth_io',lastSuccessAt:now-60,lastStagedAt:null};
  input.checks[2].state = 'attention'; input.checks[2].updateAttempt = attempt;
  assert.deepEqual(model.validateReadiness(input).checks[2].updateAttempt, attempt);
  for (const bad of [{...attempt,lastSuccessAt:now}, {...attempt,reasonCode:'no_update'}, {...attempt,finishedAt:true}, {...attempt,outcome:'current'}]) {
    assert.throws(() => model.validateReadiness({...input, checks:input.checks.map((check,index)=>index===2?{...check,updateAttempt:bad}:check)}), /invalid/i);
  }
});

after(() => rm(directory, { recursive: true, force: true }));
