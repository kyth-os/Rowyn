import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-mvp-tests-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynMvpModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

test('report preview contains explicit unavailable/error states and no hidden fields', () => {
  const report = m.formatReport({ collected_at: 0, readings: [
    { name: 'Image', state: 'unavailable', detail: 'permission denied', hidden: 'do not export' },
    { name: 'GPU', state: 'error', detail: 'check failed' },
  ] });
  assert.match(report, /Image \[unavailable\]: permission denied/);
  assert.match(report, /GPU \[error\]: check failed/);
  assert.doesNotMatch(report, /do not export/);
  assert.match(report, /Review before sharing/);
});

test('launcher selection only accepts the four curated ids', () => {
  assert.equal(m.LAUNCHERS.length, 4);
  for (const launcher of m.LAUNCHERS) assert.equal(m.requireLauncher(launcher.id).id, launcher.id);
  for (const id of ['', '--system', 'org.example.App', 'com.valvesoftware.Steam;id'])
    assert.throws(() => m.requireLauncher(id), /Unsupported launcher/);
});

test('unknown installed scopes stay unknown, while either confirmed scope is installed', () => {
  const id = m.LAUNCHERS[0].id;
  assert.equal(m.launcherState(id, null), 'unavailable');
  assert.equal(m.launcherState(id, { apps: [], failedScopes: ['system'] }), 'unavailable');
  assert.equal(m.launcherState(id, { apps: [], failedScopes: [] }), 'not installed');
  assert.equal(m.launcherState(id, { apps: [{ id, scope: 'system' }], failedScopes: ['user'] }), 'installed');
});

test('curated install preserves terminal success, failure and cancellation', async () => {
  const id = m.LAUNCHERS[0].id;
  const calls = [];
  const ops = { install: async (value) => { calls.push(value); return 'flatpak-123'; }, wait: async (job, limit) => { calls.push([job, limit]); return 'Cancelled.'; } };
  const started = [];
  assert.equal(await m.installLauncher(id, ops, (job) => started.push(job)), 'Cancelled.');
  assert.deepEqual(started, ['flatpak-123'], 'UI must receive the running job before waiting so Cancel becomes visible');
  assert.deepEqual(calls, [id, ['flatpak-123', 1800]]);
  ops.wait = async () => 'Installed successfully.';
  assert.equal(await m.installLauncher(id, ops), 'Installed successfully.');
  ops.wait = async () => { throw new Error('remote unavailable'); };
  await assert.rejects(m.installLauncher(id, ops), /remote unavailable/);
  await assert.rejects(m.installLauncher('org.unknown.App', ops), /Unsupported launcher/);
});

test('cancel owns the status even when the primary finishes late; failed cancel is visible', async () => {
  const messages = [];
  const owner = m.createPlayStatusOwner((message) => messages.push(message));
  let finish;
  const pending = owner.run('Installing…', () => new Promise((resolve) => { finish = resolve; }));
  await owner.cancel(async () => 'Cancelled.');
  finish('Installed successfully.');
  await pending;
  assert.equal(messages.at(-1), 'Cancelled.');
  await owner.run('Opening…', async () => { throw new Error('not installed'); });
  assert.equal(messages.at(-1), 'Failed: not installed');
  await owner.cancel(async () => { throw new Error('transport unavailable'); });
  assert.equal(messages.at(-1), 'Cancel failed: transport unavailable');
  await owner.run('Opening…', async () => 'Launch requested.');
  assert.equal(messages.at(-1), 'Launch requested.');
});

test('host-only options never pretend to work inside Flatpak Steam', () => {
  assert.equal(m.launchOptions('flatpak', true, true), '%command%');
  assert.equal(m.launchOptions('host', true, false), '/usr/bin/rowyn-game-launch %command%');
  assert.equal(m.launchOptions('host', false, true), '/usr/bin/rowyn-mimalloc-run %command%');
  assert.match(m.launchOptions('host', true, true), /rowyn-game-launch \/usr\/bin\/rowyn-mimalloc-run %command%/);
});

test('native pages wire explicit reads, report actions, install and cancel without timer loops', async () => {
  const diagnostics = await readFile(resolve(root, 'src/pages/RowynDiagnostics.tsx'), 'utf8');
  const play = await readFile(resolve(root, 'src/pages/RowynPlay.tsx'), 'utf8');
  for (const text of [diagnostics, play]) assert.doesNotMatch(text, /setInterval|setTimeout/);
  assert.match(diagnostics, /navigator.clipboard.writeText\(report\)/);
  assert.match(diagnostics, /exportRowynReport\(report\)/);
  assert.match(play, /waitInstallJob/);
  assert.match(play, /cancelInstall/);
  assert.match(play, /rowynControllerScan/);
  assert.match(play, /2026-10-31/);
  assert.match(play, /not Rowyn-validated/);
  assert.match(play, /ExternalLink/);
  assert.doesNotMatch(play, /target=["']_blank/);
});
