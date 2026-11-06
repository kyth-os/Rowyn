import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const directory = await mkdtemp(resolve(tmpdir(), 'rowyn-readiness-ui-'));
const outfile = resolve(directory, 'ui.cjs');
await build({ stdin: { resolveDir: root, loader: 'tsx', contents: `
  import React from 'react'; import {renderToStaticMarkup} from 'react-dom/server';
  import {StaticRouter} from 'react-router-dom';
  import {RowynReadiness,ReadinessResults} from './src/components/RowynReadiness';
  export function initial(){return renderToStaticMarkup(<StaticRouter location="/"><RowynReadiness/></StaticRouter>)}
  export function results(snapshot,busy){return renderToStaticMarkup(<ReadinessResults snapshot={snapshot} busy={busy} onAction={()=>{}}/>)}
` }, outfile, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', logLevel: 'silent' });
const loaded = await import(pathToFileURL(outfile).href);
const ui = loaded.default ?? loaded;

test('actual initial component has a searchable section, explicit read button and no invented readings', () => {
  const html = ui.initial();
  assert.match(html, /id="hub-section-readiness"/);
  assert.match(html, /System readiness/);
  assert.match(html, /class="action-button action-button-primary"[^>]*>Check readiness/);
  assert.match(html, /No checks run until/);
  assert.match(html, /Not checked yet/);
  assert.doesNotMatch(html, /Checked <time|is-ready|data-target=/);
});

test('actual result presentation exposes fixed working handoffs and distinguishes every readiness state', () => {
  const snapshot = {checkedAt: Math.floor(Date.now()/1000), checks:[
    {id:'printing', title:'Printing', state:'attention', detail:'Discovery needs attention.'},
    {id:'logging', title:'System logging', state:'ready', detail:'The system journal is running.'},
    {id:'updates', title:'Updates', state:'unavailable', detail:'The status cannot be read.'},
    {id:'backups', title:'Backups', state:'needs-setup', detail:'No plans are configured.'},
    {id:'development', title:'Development tools', state:'ready', detail:'Prerequisites are available; environments have not been tested.'},
  ]};
  const html = ui.results(snapshot, false);
  for (const label of ['Open Print settings','Open Diagnostics','Open Updates','Open Backups','Open Development']) assert.ok(html.includes(label));
  for (const label of ['Ready','Needs setup','Needs attention','Unavailable']) assert.ok(html.includes(label));
  assert.match(html, /Checked <time/);
  assert.doesNotMatch(html, /data-target=/);
  const busy = ui.results(snapshot, true);
  assert.equal((busy.match(/<button[^>]*disabled=""/g) ?? []).length, snapshot.checks.length);
});

test('actual result presentation shows attempt freshness and prior successful check without implying current health', () => {
  const snapshot = {checkedAt: Math.floor(Date.now()/1000), checks:[
    {id:'printing', title:'Printing', state:'attention', detail:'Discovery needs attention.'},
    {id:'logging', title:'System logging', state:'ready', detail:'The system journal is running.'},
    {id:'updates', title:'Updates', state:'attention', detail:'Automatic staging failed.', updateAttempt:{
      startedAt:Math.floor(Date.now()/1000)-120, finishedAt:Math.floor(Date.now()/1000)-110,
      outcome:'failed', reasonCode:'runtime_auth_io', lastSuccessAt:Math.floor(Date.now()/1000)-900, lastStagedAt:null,
    }},
    {id:'backups', title:'Backups', state:'needs-setup', detail:'No plans are configured.'},
    {id:'development', title:'Development tools', state:'ready', detail:'Prerequisites are available; environments have not been tested.'},
  ]};
  const html = ui.results(snapshot, false);
  assert.match(html, /Last automatic attempt/);
  assert.match(html, /Last successful check/);
  assert.match(html, /could not read runtime authentication metadata/i);
  assert.match(html, /last success.+does not mean this attempt succeeded/i);
});

after(() => rm(directory, {recursive:true,force:true}));
