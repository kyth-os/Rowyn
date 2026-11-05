import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-settings-')), 'settings.mjs');
await build({ entryPoints:[resolve(root,'src/services/rowynSettings.ts')], outfile:output, bundle:true, format:'esm', platform:'node', logLevel:'silent', plugins:[{
  name:'fixture-native-boundary', setup(builder) {
    builder.onResolve({filter:/^@tauri-apps\/api\/core$/},()=>({path:'native',namespace:'fixture'}));
    builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'export async function invoke(command,args){globalThis.__settingsCalls.push({command,args});return command==="rowyn_backups_snapshot"?{state:"none",detail:"Not configured",plans:[]}:"Settings launch requested."}',loader:'js'}));
  }
}] });
const model = await import(pathToFileURL(output).href);

test('browser-only calls fail visibly before invoking any native command', async () => {
  globalThis.window={}; globalThis.__settingsCalls=[];
  await assert.rejects(model.openRowynSettings('defaults'), /installed Rowyn Hub/i);
  await assert.rejects(model.fetchRowynBackupsSnapshot(), /installed Rowyn Hub/i);
  assert.deepEqual(globalThis.__settingsCalls,[]);
});

test('power settings map to the packaged KDE PowerDevil module', () => {
  assert.equal(model.SETTINGS_MODULES.power, 'kcm_powerdevil');
});

test('native settings and backup reads use only registered fixed commands', async () => {
  globalThis.window={__TAURI_INTERNALS__:{}}; globalThis.__settingsCalls=[];
  assert.match(await model.openRowynSettings('flatpak-permissions'),/requested/);
  assert.equal((await model.fetchRowynBackupsSnapshot()).state,'none');
  await assert.rejects(model.openRowynSettings('arbitrary-command'),/Unsupported/);
  assert.deepEqual(globalThis.__settingsCalls,[{command:'rowyn_open_settings',args:{target:'flatpak-permissions'}},{command:'rowyn_backups_snapshot',args:undefined}]);
  delete globalThis.window;
});
