import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-work-tests-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynWorkModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

test('queue labels keep an unreported state unknown instead of calling it ready', () => {
  assert.match(m.queueStateLabel('idle'), /ready to print/);
  assert.match(m.queueStateLabel('printing'), /printing now/);
  assert.match(m.queueStateLabel('disabled'), /held/);
  for (const state of ['', 'paused?', 'on fire'])
    assert.match(m.queueStateLabel(state), /unknown/);
});

test('queue summaries name the queues and the default, and unknown is not empty', () => {
  const parsed = { queues: [{ name: 'Office_Printer', state: 'idle', is_default: true }], default_queue: 'Office_Printer', detail: '' };
  const summary = m.queuesSummary(parsed);
  assert.match(summary, /Office_Printer/);
  assert.match(summary, /Default queue: Office_Printer\./);
  assert.match(m.queuesSummary(null), /unknown rather than empty/);
  assert.match(m.queuesSummary({ queues: [], default_queue: null, detail: '' }), /No print queues are configured/);
  const noDefault = m.queuesSummary({ queues: [{ name: 'A', state: 'idle', is_default: false }], default_queue: null, detail: '' });
  assert.match(noDefault, /No default queue is set/);
});

test('the test-page prompt names its queue and leaves confirmation to the printer', () => {
  const prompt = m.testPagePrompt('Office_Printer');
  assert.match(prompt, /Office_Printer/);
  assert.match(prompt, /test page/i);
  assert.match(prompt, /printer/i);
});

test('discovery separates addable IPP addresses from queues that must not be rewritten', () => {
  const parsed = m.parseDiscoveredPrinters([
    'ipp://Office%20Printer._ipp._tcp.local/',
    'ipps://192.168.1.10:631/ipp/print',
    'Office_Printer',
    'not a printer',
    'ipp://Office%20Printer._ipp._tcp.local/',
  ]);
  assert.deepEqual(parsed.addable.map((p) => p.name), ['Office_Printer', '192.168.1.10']);
  assert.equal(parsed.addable[0].uri, 'ipp://Office%20Printer._ipp._tcp.local/');
  assert.deepEqual(parsed.existing, ['Office_Printer']);
  assert.deepEqual(parsed.unsupported, ['not a printer']);
  // An address without a host cannot be named, so it is reported, not added.
  assert.deepEqual(m.parseDiscoveredPrinters(['ipp:///ipp/print']).addable, []);
});

test('the add-queue prompt names the queue, the address and the authorization caveat', () => {
  const prompt = m.addQueuePrompt('Office_Printer', 'ipp://office.local/ipp/print');
  assert.match(prompt, /Office_Printer/);
  assert.match(prompt, /ipp:\/\/office\.local\/ipp\/print/);
  assert.match(prompt, /driverless/);
  assert.match(prompt, /administrator/);
  assert.match(prompt, /existing queues are not changed/);
});

test('the work page adds a queue only from its own confirmation card', async () => {
  const page = await readFile(resolve(root, 'src/pages/RowynWork.tsx'), 'utf8');
  assert.match(page, /addPrintQueue/);
  assert.match(page, /Add print queue/);
  assert.match(page, /id="rowyn-work-add-queue"/);
  assert.match(page, /parseDiscoveredPrinters/);
  assert.doesNotMatch(page, /window\.confirm|confirmUserAction/);
});

test('the work page confirms in its own card and never polls or uses a native dialog', async () => {
  const page = await readFile(resolve(root, 'src/pages/RowynWork.tsx'), 'utf8');
  assert.doesNotMatch(page, /setInterval|setTimeout/);
  assert.doesNotMatch(page, /confirmUserAction|window\.confirm/);
  assert.doesNotMatch(page, /target=["']_blank/);
  assert.match(page, /role="alertdialog"/);
  assert.match(page, /Print test page/);
  assert.match(page, /Open printer settings/);
  assert.match(page, /useSectionFollow\(SECTIONS\)/);
  assert.match(page, /ipp-usb/);
  assert.match(page, /<h1>Work<\/h1>/);
  // The printing card is a section of the page, not the page title.
  assert.match(page, /<h2>Printing<\/h2>/);
  for (const anchor of ['hub-section-printing', 'hub-section-office', 'hub-section-focus'])
    assert.equal([...page.matchAll(new RegExp(`id="${anchor}"`, 'g'))].length, 1, anchor);
});

test('the work page reads its state on open and never claims a page printed', async () => {
  const page = await readFile(resolve(root, 'src/pages/RowynWork.tsx'), 'utf8');
  assert.match(page, /useEffect\(\(\) => \{/);
  assert.match(page, /readPrintQueues\(\)/);
  assert.match(page, /rowyn_print_test_page|printTestPage/);
  // "A queued job is not a printed page" is the honest wording; what must
  // never appear is a claim that the page came out.
  assert.doesNotMatch(page, /printed successfully|printing succeeded|page came out/i);
});
