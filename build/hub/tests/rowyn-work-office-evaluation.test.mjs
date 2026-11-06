import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = await readFile(resolve(root, 'src/services/liveData.ts'), 'utf8');
const tauriEnv = await readFile(resolve(root, 'src/services/tauriEnv.ts'), 'utf8');
const rust = await readFile(resolve(root, 'src-tauri/src/main.rs'), 'utf8');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-work-office-')), 'liveData.mjs');
await build({ entryPoints:[resolve(root,'src/services/liveData.ts')], outfile:out, bundle:true, format:'esm', platform:'node', logLevel:'silent', plugins:[{ name:'fixture-ipc', setup(b){ b.onResolve({filter:/^@tauri-apps\/api\/core$/},()=>({path:'fixture',namespace:'fixture'})); b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'export async function invoke(command,args){globalThis.__calls.push({command,args});if(globalThis.__reject?.includes(command))throw Error("fixture failure");return globalThis.__results?.[command]}',loader:'js'})); }}] });
const svc = await import(pathToFileURL(out).href);
const native = () => { globalThis.window={__TAURI_INTERNALS__:{}}; globalThis.__calls=[]; globalThis.__results={}; globalThis.__reject=[]; };

test('browser mode returns unavailable/null for office, mail, and focus reads with zero IPC', async()=>{
 globalThis.window={}; globalThis.__calls=[];
 assert.equal(await svc.fetchPstFiles(),null); assert.equal(await svc.fetchFocusStatus(),null);
 for (const fn of [()=>svc.openM365App('Outlook'),()=>svc.createM365Shortcuts(),()=>svc.startFocusSession(25),()=>svc.stopFocusSession('fixture-id')]) await assert.rejects(fn,/installed Rowyn Hub/);
 assert.deepEqual(globalThis.__calls,[]); delete globalThis.window;
});

test('Office web app buttons and shortcuts use registered command argument shapes', async()=>{
 native(); globalThis.__results.open_m365_app='Opened in browser.'; globalThis.__results.create_m365_shortcuts='Added shortcuts.';
 const names = ['Outlook','Word','Excel','PowerPoint','OneNote','Teams'];
 for (const name of names) assert.equal(await svc.openM365App(name),'Opened in browser.');
 assert.equal(await svc.createM365Shortcuts(),'Added shortcuts.');
 assert.deepEqual(globalThis.__calls,[...names.map(name=>({command:'open_m365_app',args:{name}})),{command:'create_m365_shortcuts',args:undefined}]);
 assert.match(rust,/fn open_m365_app\(name: String\)/); assert.match(rust,/fn create_m365_shortcuts\(\)/);
});

test('PST scan null-on-failure and empty-list contract are preserved',async()=>{
 native(); globalThis.__results.pst_files=[]; assert.deepEqual(await svc.fetchPstFiles(),[]);
 globalThis.__reject=['pst_files']; assert.equal(await svc.fetchPstFiles(),null);
 assert.deepEqual(globalThis.__calls,[{command:'pst_files',args:undefined},{command:'pst_files',args:undefined}]);
 assert.match(rust,/fn pst_files\(\) -> Vec<String>/);
});

test('Focus status/start/stop use typed bridge arguments and surface mutations',async()=>{
 native(); globalThis.__results.focus_status={id:'focus-123',remaining_secs:600}; globalThis.__results.focus_start='focus-456'; globalThis.__results.focus_stop='Focus session already ended.';
 assert.deepEqual(await svc.fetchFocusStatus(),{id:'focus-123',remaining_secs:600});
 assert.equal(await svc.startFocusSession(25),'focus-456'); assert.equal(await svc.stopFocusSession('focus-456'),'Focus session already ended.');
 assert.deepEqual(globalThis.__calls,[{command:'focus_status',args:undefined},{command:'focus_start',args:{minutes:25}},{command:'focus_stop',args:{id:'focus-456'}}]);
 globalThis.__reject=['focus_status']; assert.equal(await svc.fetchFocusStatus(),null);
 assert.match(rust,/fn focus_status\(\) -> Result<Option<FocusStatus>, String>/); assert.match(rust,/fn focus_start\(minutes: u32\)/); assert.match(rust,/fn focus_stop\(id: String\)/);
});

test('Office install bridge passes a fixed app ID; caller wiring waits for the terminal job',async()=>{
 native(); globalThis.__results.install_flatpak={job:'install-123',state:'running',detail:'Installing'};
 assert.equal(await svc.installFlatpak('org.libreoffice.LibreOffice'),'install-123');
 assert.deepEqual(globalThis.__calls,[{command:'install_flatpak',args:{app_id:'org.libreoffice.LibreOffice'}}]);
 const workPage = await readFile(resolve(root, 'src/pages/RowynWork.tsx'), 'utf8');
 assert.match(workPage,/waitInstallJob\(await installFlatpak\(id\)\)/); assert.match(workPage,/detail === 'Installed successfully\.'/);
 assert.match(tauriEnv,/"__TAURI_INTERNALS__" in window/);
});
