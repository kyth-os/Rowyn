import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const outfile=resolve(await mkdtemp(resolve(tmpdir(),'rowyn-removal-')),'test.cjs');
await build({stdin:{contents:`import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server'; import {AppRemovalConfirmation,canConfirmRemoval} from './src/components/AppRemovalConfirmation'; export {canConfirmRemoval}; export const markup=renderToStaticMarkup(React.createElement(AppRemovalConfirmation,{app:{id:'org.example.App',name:'Example'},busy:false,onCancel(){},onConfirm(){}}));`,resolveDir:root,loader:'tsx'},outfile,bundle:true,platform:'node',format:'cjs',jsx:'automatic',logLevel:'silent'});
const loaded=await import(pathToFileURL(outfile).href);
const model=loaded.default ?? loaded;
test('removal is fail-closed without acknowledgement, with busy state, or invalid identity',()=>{
 assert.equal(model.canConfirmRemoval(false,false,'org.example.App'),false);
 assert.equal(model.canConfirmRemoval(true,true,'org.example.App'),false);
 assert.equal(model.canConfirmRemoval(true,false,'--all'),false);
 assert.equal(model.canConfirmRemoval(true,false,'org.example.App'),true);
});
test('removal preview names target, warns shared/system scope, and starts disabled',()=>{
 assert.match(model.markup,/org\.example\.App/);
 assert.match(model.markup,/role="alertdialog"/);
 assert.match(model.markup,/other users/);
 assert.match(model.markup,/personal files/);
 assert.match(model.markup,/not a guaranteed amount/);
 assert.match(model.markup,/<button[^>]*disabled=""[^>]*>Confirm removal/);
});
