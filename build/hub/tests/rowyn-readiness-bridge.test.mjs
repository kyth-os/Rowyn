import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const directory = await mkdtemp(resolve(tmpdir(), 'rowyn-readiness-bridge-'));
const outfile = resolve(directory, 'bridge.mjs');
globalThis.__readyImports = 0;
globalThis.__readyCalls = [];
await build({ entryPoints: [resolve(root, 'src/services/rowynReadiness.ts')], outfile,
  bundle: true, format: 'esm', platform: 'node', logLevel: 'silent', plugins: [{
    name: 'readiness-ipc-fixture', setup(builder) {
      builder.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: 'ipc', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ loader: 'js', contents: `
        globalThis.__readyImports++;
        export async function invoke(command, args) {
          globalThis.__readyCalls.push({command, args});
          if (globalThis.__readyError) throw Error('fixture unavailable');
          return globalThis.__readyOutput;
        }` }));
    },
  }] });
const service = await import(pathToFileURL(outfile).href);
const fixture = () => ({ checkedAt: Math.floor(Date.now() / 1000), checks: [
  ['printing', 'Printing'], ['logging', 'System logging'], ['updates', 'Updates'],
  ['backups', 'Backups'], ['development', 'Development tools'],
].map(([id, title]) => ({ id, title, state: 'unavailable', detail: 'Fixture read unavailable.' })) });

test('browser mode does not import native IPC or invoke any command', async () => {
  globalThis.window = {};
  await assert.rejects(service.rowynReadinessSnapshot(), /installed Rowyn Hub/);
  assert.equal(globalThis.__readyImports, 0);
  assert.deepEqual(globalThis.__readyCalls, []);
});

test('native mode uses the actual fixed registered command and validates the response', async () => {
  globalThis.window = { __TAURI_INTERNALS__: {} };
  const output = fixture(); globalThis.__readyOutput = output;
  assert.deepEqual(await service.rowynReadinessSnapshot(), output);
  assert.deepEqual(globalThis.__readyCalls, [{ command: 'rowyn_readiness_snapshot', args: undefined }]);
  globalThis.__readyOutput = { ...output, checks: [] };
  await assert.rejects(service.rowynReadinessSnapshot(), /invalid/);
});

test('native rejection settles visibly and clears the outstanding deadline', async (context) => {
  const clear = context.mock.method(globalThis, 'clearTimeout');
  globalThis.__readyError = true;
  try { await assert.rejects(service.rowynReadinessSnapshot(), /fixture unavailable/); }
  finally { globalThis.__readyError = false; }
  assert.equal(clear.mock.calls.length, 1);
});

test('a stalled bridge is bounded, with enough time for the complete native probe budget', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  globalThis.__readyOutput = new Promise(() => {});
  let settled = false;
  const result = service.rowynReadinessSnapshot().then(
    () => { settled = true; return null; },
    (error) => { settled = true; return error; },
  );
  await Promise.resolve(); await Promise.resolve();
  context.mock.timers.tick(24_999);
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(settled, false, 'the frontend must not expire before the native probe budget');
  context.mock.timers.tick(2);
  assert.match((await result).message, /timed out/);
});

after(async () => { delete globalThis.window; await rm(directory, { recursive: true, force: true }); });
