import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(await mkdtemp(resolve(tmpdir(), 'rowyn-development-')), 'model.mjs');
await build({ entryPoints: [resolve(root, 'src/services/rowynDevelopmentModel.ts')], outfile: out, bundle: true, format: 'esm', platform: 'node', logLevel: 'silent' });
const m = await import(pathToFileURL(out).href);

test('managed image is digest-pinned and names the apt security floor', () => {
  assert.match(m.DEVELOPMENT_IMAGE, /^docker\.io\/library\/ubuntu@sha256:[0-9a-f]{64}$/);
  assert.equal(m.APT_FLOOR, '3.3.3ubuntu0.1');
  assert.match(m.DEVELOPMENT_IMAGE_LABEL, /Ubuntu 26\.10/);
});

test('page states the digest pin and the pre-preset apt upgrade', async () => {
  const page = await readFile(resolve(root, 'src/pages/RowynDevelopment.tsx'), 'utf8');
  assert.match(page, /DEVELOPMENT_IMAGE_LABEL/);
  assert.match(page, /USN-8996-1/);
  assert.match(page, /CVE-2026-109518/);
});

test('fixed presets expose their pinned package plans and disallow arbitrary package input', () => {
  assert.deepEqual(m.DEVELOPMENT_PRESETS.map(p => [p.id, p.packages]), [
    ['git-build', ['git', 'build-essential', 'pkg-config', 'cmake']],
    ['python', ['python3', 'python3-pip', 'python3-venv']],
    ['node', ['nodejs', 'npm']], ['rust', ['rustc', 'cargo']],
  ]);
  assert.equal(m.presetOperation('git-build'), 'preset:git-build');
  assert.equal(m.presetOperation('not-allowed'), null);
});

test('page presents concrete plan, confirmation, phases, cancellation and partial-state warning', async () => {
  const page = await readFile(resolve(root, 'src/pages/RowynDevelopment.tsx'), 'utf8');
  assert.match(page, /plan\.commands\.map/);
  assert.match(page, /role="alertdialog"/);
  assert.match(page, /confirmPending/);
  assert.doesNotMatch(page, /confirmUserAction|window\.confirm/);
  assert.match(page, /partial.*package|package.*partial/i);
});

test('Dolphin service menu passes one directory argument to the fixed executable', async () => {
  const menu = await readFile(resolve(root, 'src-tauri/data/kde/servicemenus/rowyn-development-here.desktop'), 'utf8');
  assert.match(menu, /Exec=\/usr\/bin\/rowyn-hub-shell --development-directory %f/);
  assert.match(menu, /MimeType=inode\/directory/);
});

test('states separate managed, absent, collision and unavailable', () => {
  assert.equal(m.describeState({ state: 'absent' }).canCreate, true);
  assert.equal(m.describeState({ state: 'managed' }).canOpen, true);
  assert.equal(m.describeState({ state: 'managed' }).canDelete, true);
  assert.equal(m.describeState({ state: 'collision' }).canCreate, false);
  assert.equal(m.describeState({ state: 'unavailable', detail: 'permission denied' }).message, 'permission denied');
  assert.equal(m.describeState({ state: 'unknown' }).canCreate, false);
});

test('delete requires exact fixed name and confirmation warns about package loss', () => {
  assert.equal(m.deleteConfirmation('rowyn-ubuntu-dev'), null);
  assert.match(m.deleteConfirmation('wrong'), /Type rowyn-ubuntu-dev/);
  assert.match(m.DELETE_WARNING, /installed packages.*home.*retained/i);
});

test('polling is bounded and only continues for running jobs', () => {
  assert.equal(m.shouldPoll({ status: 'running' }), true);
  for (const status of ['succeeded', 'failed', 'cancelled']) assert.equal(m.shouldPoll({ status }), false);
  assert.equal(m.POLL_LIMIT, 1800);
});

test('starting a job activates polling and terminal details survive refresh', async () => {
  const job = { id: 'job-1', status: 'running', detail: 'Creating' };
  let polled = 0, message = '', refreshes = 0;
  const ctl = m.createJobController({ start: async () => job, poll: async () => { polled++; return { ...job, status: 'failed', detail: 'Container creation failed: disk full' }; }, cancel: async () => ({ ...job, status: 'cancelled', detail: 'Cancelled' }), onJob: () => {}, onMessage: x => { message = x; }, refresh: async () => { refreshes++; }, intervalMs: 1 });
  await ctl.start('create'); await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(polled, 1); assert.equal(message, 'Container creation failed: disk full'); assert.equal(refreshes, 1); ctl.dispose();
});

test('cancel invalidates an in-flight poll so stale status cannot overwrite it', async () => {
  let resolvePoll, resolveCancel, message = '', jobs = [];
  const ctl = m.createJobController({ start: async () => ({id:'race',status:'running',detail:'Starting'}), poll: () => new Promise(resolve => { resolvePoll = resolve; }), cancel: () => new Promise(resolve => { resolveCancel = resolve; }), onJob: j => jobs.push(j), onMessage: x => { message = x; }, refresh: async () => {}, intervalMs: 1 });
  await ctl.start('create'); await new Promise(resolve => setTimeout(resolve, 5));
  const cancelling = ctl.cancel(); resolveCancel({id:'race',status:'cancelled',detail:'Cancelled; partial environment may remain.'}); await cancelling;
  resolvePoll({id:'race',status:'succeeded',detail:'Late success'}); await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(ctl.job.status, 'cancelled'); assert.equal(message, 'Cancelled; partial environment may remain.'); assert.equal(jobs.at(-1).status, 'cancelled'); ctl.dispose();
});

test('running cancel acknowledgment continues polling to terminal status', async () => {
  let polled = 0, message = '';
  const ctl = m.createJobController({
    start: async () => ({ id: 'cancel-poll', status: 'running', detail: 'Starting' }),
    poll: async () => { polled++; return { id: 'cancel-poll', status: 'cancelled', detail: 'Cancelled finally' }; },
    cancel: async () => ({ id: 'cancel-poll', status: 'running', detail: 'Cancellation requested' }),
    onJob: () => {}, onMessage: value => { message = value; }, refresh: async () => {}, intervalMs: 1,
  });
  await ctl.start('create');
  await ctl.cancel();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(polled, 1);
  assert.equal(ctl.job.status, 'cancelled');
  assert.equal(message, 'Cancelled finally');
  ctl.dispose();
});

test('cancel after polling pauses resumes status checks when cancellation remains running', async () => {
  let polls = 0;
  const ctl = m.createJobController({
    start: async () => ({ id: 'paused-cancel', status: 'running', detail: 'Starting' }),
    poll: async () => { polls++; if (polls === 1) throw Error('offline'); return { id: 'paused-cancel', status: 'cancelled', detail: 'Cancelled finally' }; },
    cancel: async () => ({ id: 'paused-cancel', status: 'running', detail: 'Cancellation requested' }),
    onJob: () => {}, onMessage: () => {}, refresh: async () => {}, intervalMs: 1, maxPollErrors: 1,
  });
  await ctl.start('create');
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(ctl.paused, true);
  const beforeCancelPolls = polls;
  await ctl.cancel();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(ctl.paused, false);
  assert.equal(polls, beforeCancelPolls + 1);
  assert.equal(ctl.job.status, 'cancelled');
  ctl.dispose();
});
test('overlapping starts are rejected synchronously and disposed controllers ignore pending starts', async () => {
  let resolveStart, starts = 0, updates = 0;
  const ctl = m.createJobController({ start: () => { starts++; return new Promise(resolve => { resolveStart = resolve; }); }, poll: async () => ({id:'x',status:'running',detail:''}), cancel: async () => ({id:'x',status:'cancelled',detail:''}), onJob: () => { updates++; }, onMessage: () => { updates++; }, refresh: async () => {}, intervalMs: 1 });
  const first = ctl.start('create'), second = ctl.start('delete'); assert.equal(starts, 1); ctl.dispose(); resolveStart({id:'x',status:'running',detail:'Started'}); await Promise.all([first,second]); assert.equal(updates, 0);
});

test('resume polls an existing job without starting another operation', async () => {
  let starts = 0, polls = 0;
  const ctl = m.createJobController({ start: async () => { starts++; throw Error('must not start'); }, poll: async id => { polls++; return { id, status: 'succeeded', detail: 'Resumed result' }; }, cancel: async id => ({ id, status: 'cancelled', detail: 'Cancelled' }), onJob: () => {}, onMessage: () => {}, refresh: async () => {}, intervalMs: 1 });
  await ctl.resume('persisted-job');
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(starts, 0); assert.equal(polls, 1); assert.equal(ctl.job.status, 'succeeded'); ctl.dispose();
});

test('terminal detail is restored after state-only refresh and running helper gates actions', async () => {
  assert.equal(m.isRunningJob({ id: 'x', status: 'running', detail: '' }), true);
  for (const status of ['succeeded', 'failed', 'cancelled']) assert.equal(m.isRunningJob({ id: 'x', status, detail: '' }), false);
  let message = '';
  const ctl = m.createJobController({ start: async () => ({ id: 'x', status: 'failed', detail: 'Specific failure detail' }), poll: async id => ({ id, status: 'running', detail: '' }), cancel: async id => ({ id, status: 'cancelled', detail: '' }), onJob: () => {}, onMessage: value => { message = value; }, refresh: async () => { message = 'State refreshed'; }, intervalMs: 1 });
  await ctl.start('create'); await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(message, 'Specific failure detail'); ctl.dispose();
});

test('open completion after dispose publishes nothing', async () => {
  let resolveOpen, updates = 0;
  const ctl = m.createJobController({ start: async () => { throw Error(); }, poll: async id => ({ id, status: 'running', detail: '' }), cancel: async id => ({ id, status: 'cancelled', detail: '' }), onJob: () => {}, onMessage: () => { updates++; }, refresh: async () => {}, intervalMs: 1 });
  const opening = ctl.open(() => new Promise(resolve => { resolveOpen = resolve; })); ctl.dispose(); resolveOpen('Opened'); await opening; assert.equal(updates, 0);
});

test('poll failures pause after the configured bound and can be resumed', async () => {
  let polls = 0, messages = [];
  const ctl = m.createJobController({ start: async () => ({ id: 'x', status: 'running', detail: 'Starting' }), poll: async () => { polls++; throw Error('offline'); }, cancel: async id => ({ id, status: 'cancelled', detail: '' }), onJob: () => {}, onMessage: value => messages.push(value), refresh: async () => {}, intervalMs: 1, maxPollErrors: 2 });
  await ctl.start('create'); await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(polls, 2); assert.equal(ctl.paused, true); assert.match(messages.at(-1), /resume/i);
  await ctl.resumePolling(); await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(polls, 4); assert.equal(ctl.paused, true); ctl.dispose();
});

test('page requires explicit refresh and documents Distrobox boundary without arbitrary commands', async () => {
  const page = await readFile(resolve(root, 'src/pages/RowynDevelopment.tsx'), 'utf8');
  assert.match(page, /Refresh status/);
  assert.match(page, /not a sandbox/i);
  assert.match(page, /shares your home/i);
  assert.match(page, /role="alertdialog"/);
  assert.doesNotMatch(page, /confirmUserAction|window\.confirm/);
  assert.doesNotMatch(page, /setInterval|setTimeout|type .*command|shell input/i);
});
