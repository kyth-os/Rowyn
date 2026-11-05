import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dir = await mkdtemp(resolve(root, '.care-fixture-'));
const require = createRequire(import.meta.url);
const bridgeOut = resolve(dir, 'bridge.cjs');
await build({ entryPoints:[resolve(root,'src/services/rowynCare.ts')], outfile:bridgeOut, bundle:true, format:'cjs', platform:'node', logLevel:'silent', plugins:[{name:'care-ipc-fixture',setup(b){b.onResolve({filter:/^@tauri-apps\/api\/core$/},()=>({path:'native',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'exports.invoke=async(command,args)=>{globalThis.__careCalls.push({command,args});if(globalThis.__careReject)throw new Error("fixture rejected");return globalThis.__carePayload}',loader:'js'}));}}] });
const bridge = require(bridgeOut);
const uiOut = resolve(dir, 'ui.cjs');
await build({ entryPoints:[resolve(root,'src/components/SystemCare.tsx')], outfile:uiOut, bundle:true, format:'cjs', platform:'node', jsx:'automatic', external:['react','react-dom'], logLevel:'silent' });
const ui = require(uiOut);
const { createElement } = require('react');
const { renderToString } = require('react-dom/server');

test('real care components SSR with hash navigation and pre-snapshot settings action',async()=>{
 const storage=renderToString(createElement(ui.StorageCare));
 const power=renderToString(createElement(ui.PowerCare));
 const { readFile } = await import('node:fs/promises');
 const source=await readFile(resolve(root,'src/components/SystemCare.tsx'),'utf8');
 assert.match(source,/href="#\/apps"/); assert.match(source,/href="#\/development"/);
 assert.match(source,/composefs are not physical capacity measures/);
 assert.match(storage,/hub-section-storage/); assert.match(power,/hub-section-power/);
 assert.match(power,/<button class="action-button">Power settings<\/button>/);
});
test('care bridge rejects outside shell without native IPC',async()=>{
 globalThis.window={};globalThis.__careCalls=[];
 await assert.rejects(bridge.rowynStorageSnapshot(),/installed Rowyn Hub/i);
 assert.deepEqual(globalThis.__careCalls,[]);
});
test('care bridge invokes fixed snapshot commands and parses payloads',async()=>{
 globalThis.window={__TAURI_INTERNALS__:{}};globalThis.__careCalls=[];globalThis.__carePayload={state:'ok',volumes:[],inventory:[]};
 assert.equal((await bridge.rowynStorageSnapshot()).state,'ok');
 globalThis.__carePayload={state:'ok',batteries:[],chargeLimit:{state:'unsupported',value:null},profile:{state:'unavailable',value:null},capabilities:{state:'unsupported',value:[]}};
 assert.equal((await bridge.rowynPowerSnapshot()).state,'ok');
 assert.deepEqual(globalThis.__careCalls.map(x=>x.command),['rowyn_storage_snapshot','rowyn_power_snapshot']);
 globalThis.__careReject=true; await assert.rejects(bridge.rowynStorageSnapshot(),/fixture rejected/);
 delete globalThis.window;
});
