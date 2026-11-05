import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-setup-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynSetupChecklistModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const model = await import(pathToFileURL(out).href);
const inMemoryStorage = () => {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
};

test('versioned parser validates fields, filters unknown IDs, deduplicates, and resolves conflicting status', () => {
  assert.deepEqual(model.parseChecklist('{'), model.emptyChecklist());
  assert.deepEqual(model.parseChecklist(JSON.stringify({ version: 2, completed: [], skipped: [], dismissed: false })), model.emptyChecklist());
  assert.deepEqual(model.parseChecklist(JSON.stringify({ version: 1, completed: ['apps', 'apps', 'bogus'], skipped: ['apps', 'updates', 'updates', 'bogus'], dismissed: false })), { version: 1, completed: ['apps'], skipped: ['updates'], dismissed: false });
});

test('storage reads and writes survive unavailable or throwing storage', () => {
  assert.deepEqual(model.loadChecklist(null), model.emptyChecklist());
  assert.deepEqual(model.loadChecklist({ getItem() { throw Error('denied'); } }), model.emptyChecklist());
  assert.equal(model.saveChecklist(model.emptyChecklist(), null), false);
  assert.equal(model.saveChecklist(model.emptyChecklist(), { setItem() { throw Error('denied'); } }), false);
});

test('dismissal and item states persist and reload; manual completion and skip are mutually exclusive', () => {
  const storage = inMemoryStorage();
  let state = model.loadChecklist(storage);
  state = model.setChecklistItem(state, 'apps', 'completed');
  assert.deepEqual(state.completed, ['apps']);
  state = model.setChecklistItem(state, 'apps', 'skipped');
  assert.deepEqual(state.completed, []);
  assert.deepEqual(state.skipped, ['apps']);
  state = { ...state, dismissed: true };
  assert.equal(model.saveChecklist(state, storage), true);
  assert.deepEqual(model.loadChecklist(storage), state);
});

test('invalid IDs are ignored and component mount has no completion side effects', async () => {
  const state = model.emptyChecklist();
  assert.equal(model.setChecklistItem(state, 'bogus', 'completed'), state);
  assert.deepEqual(state.completed, []);
  const component = await readFile(resolve(root, 'src/pages/SetupChecklist.tsx'), 'utf8');
  assert.match(component, /useState<ChecklistState>\(\(\) => loadChecklist\(\)\)/);
  assert.doesNotMatch(component, /useEffect/);
  assert.match(component, /onClick=\{\(\) => update\(setChecklistItem\(state, item\.id, 'completed'\)\)\}/);
  assert.match(component, /saveChecklist\(next\)/);
  assert.match(component, /session only/);
});

test('checklist links name existing application routes only', () => {
  const routes = ['/apps', '/updates', '/development', '/diagnostics', '/backups'];
  assert.deepEqual(model.CHECKLIST_ITEMS.map(item => item.route), routes);
  assert.equal(new Set(model.CHECKLIST_ITEMS.map(item => item.route)).size, model.CHECKLIST_ITEMS.length);
});

test('settings targets and Apps action display real launch errors and pending state', async () => {
  const settings = await readFile(resolve(root, 'src/services/rowynSettings.ts'), 'utf8');
  const apps = await readFile(resolve(root, 'src/pages/AppsDefaultsAccess.tsx'), 'utf8');
  assert.match(settings, /kcm_app-permissions/);
  assert.match(settings, /backups: 'kcm_kup'/);
  assert.match(apps, /role=\{result\.error \? 'alert' : 'status'\}/);
  assert.match(apps, /disabled=\{pending !== null\}/);
  assert.doesNotMatch(apps, /catch\(\(\) => \{\}\)/);
});
