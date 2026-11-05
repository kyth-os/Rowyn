import { useEffect, useRef, useState } from 'react';
import {
  getDevelopmentActiveJobId,
  rowynDevelopmentList,
  rowynDevelopmentStart,
  rowynDevelopmentJob,
  rowynDevelopmentCancel,
  rowynDevelopmentOpen,
  rowynDevelopmentPresetPreview,
  setDevelopmentActiveJobId,
  type DevelopmentJob,
} from '../services/rowynDevelopment';
import {
  createJobController,
  DEVELOPMENT_NAME,
  DEVELOPMENT_IMAGE_LABEL,
  DEVELOPMENT_PRESETS,
  presetOperation,
  DELETE_WARNING,
  describeState,
  deleteConfirmation,
  type DevelopmentState,
} from '../services/rowynDevelopmentModel';
import { useSectionFollow, type SectionMap } from '../hooks/useSectionFollow';
import '../styles/rowynMvp.css';

const SECTIONS: SectionMap = { Development: 'hub-section-development' };

type PageState = DevelopmentState & {
  status?: string;
  name?: string;
  image?: string;
};

// Confirmation happens in the page, not through a script dialog: a webview
// that does not implement one answers false, which made every lifecycle
// button look dead.
type PendingAction =
  | { kind: 'create' }
  | { kind: 'delete' }
  | { kind: 'preset'; preset: string; target: string; packages: string[] };

function presetLabel(id: string): string {
  return DEVELOPMENT_PRESETS.find(item => item.id === id)?.label ?? id;
}

function pendingTitle(pending: PendingAction): string {
  if (pending.kind === 'create') return `Create ${DEVELOPMENT_NAME}?`;
  if (pending.kind === 'delete') return `Delete ${DEVELOPMENT_NAME}?`;
  return `Install ${presetLabel(pending.preset)}?`;
}

function pendingCopy(pending: PendingAction): string {
  if (pending.kind === 'create') {
    return `Creates the digest-pinned ${DEVELOPMENT_IMAGE_LABEL} container and installs the fixed APT (USN-8996-1) first. The first run pulls the image, so it can take several minutes.`;
  }
  if (pending.kind === 'delete') return DELETE_WARNING;
  return `Installs ${pending.packages.join(', ')} in ${pending.target}. Cancelling stops the process group where possible, but package and apt changes already made are not rolled back.`;
}

function pendingConfirmLabel(pending: PendingAction): string {
  if (pending.kind === 'create') return 'Create environment';
  if (pending.kind === 'delete') return 'Delete environment';
  return 'Install packages';
}

export function Development() {
  useSectionFollow(SECTIONS);
  const [status, setStatus] = useState<PageState | null>(null);
  const [message, setMessage] = useState('Refresh status to inspect the dedicated environment.');
  const [job, setJob] = useState<DevelopmentJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [preset, setPreset] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [plan, setPlan] = useState<{ preset: string; target: string; packages: string[]; commands: string[][] } | null>(null);
  const mounted = useRef(false);
  const lock = useRef(false);
  const controller = useRef<ReturnType<typeof createJobController> | null>(null);

  useEffect(() => {
    mounted.current = true;
    const ctl = createJobController({
      start: rowynDevelopmentStart,
      poll: rowynDevelopmentJob,
      cancel: rowynDevelopmentCancel,
      onJob: (next) => {
        if (!mounted.current) return;
        setJob(next);
        setDevelopmentActiveJobId(next?.status === 'running' ? next.id : null);
      },
      onActiveId: setDevelopmentActiveJobId,
      onPaused: (paused) => {
        if (mounted.current && paused) setMessage('Job polling paused. Resume status updates to continue.');
      },
      onMessage: (next) => {
        if (mounted.current) setMessage(next);
      },
      refresh: async () => {
        try {
          const next = await rowynDevelopmentList();
          if (mounted.current) setStatus(next);
        } catch (error) {
          if (mounted.current) {
            setStatus({ state: 'unavailable', detail: 'Development environment status is unavailable.' });
          }
          throw new Error(`Refresh status unavailable: ${error instanceof Error ? error.message : String(error)}`);
        }
      },
    });
    controller.current = ctl;
    const activeId = getDevelopmentActiveJobId();
    if (activeId) void ctl.resume(activeId);
    else void ctl.refresh().catch((error) => {
      if (mounted.current) setMessage(String(error));
    });

    return () => {
      mounted.current = false;
      ctl.dispose();
      controller.current = null;
    };
  }, []);

  async function act(fn: () => Promise<unknown>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      await fn();
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const caps = describeState(status);
  const running = job?.status === 'running';
  const lockedActions = busy || running;

  async function confirmPending() {
    const action = pending;
    if (!action) return;
    setPending(null);
    if (action.kind === 'create') {
      void act(() => controller.current?.start('create') ?? Promise.resolve());
      return;
    }
    if (action.kind === 'delete') {
      void act(async () => {
        await controller.current?.start('delete', confirmation);
        if (mounted.current && controller.current?.job?.status !== 'failed') setConfirmation('');
      });
      return;
    }
    const operation = presetOperation(action.preset);
    if (operation) void act(() => controller.current?.start(operation, `install:${action.preset}`) ?? Promise.resolve());
  }

  return (
    <div id="hub-section-development" className="page-content rowyn-mvp">
      <section className="glass dashboard-card">
        <span className="home-eyebrow">Rowyn Development</span>
        <h1>Ubuntu development environment</h1>
        <p className="card-copy">
          One dedicated Distrobox named <code>{DEVELOPMENT_NAME}</code> from{' '}
          <code>{DEVELOPMENT_IMAGE_LABEL}</code>. Tools stay in the container rather than the immutable host
          OS. Distrobox shares your home directory and is <strong>not a sandbox</strong>.
        </p>
        <p className="card-copy">
          Only this fixed environment is managed: no shell commands, no arbitrary containers. Creating it
          and every preset install the fixed APT (USN-8996-1) first, because no published image carries
the CVE-2026-109518 fix yet; cancelling does not roll back changes
          already made. The first terminal open may initialize the container and download packages.
        </p>
        <div className="home-actions">
          {DEVELOPMENT_PRESETS.map(item => (
            <button key={item.id} className="action-button" disabled={lockedActions || !caps.canOpen}
              onClick={() => void act(async () => {
                setPreset(item.id);
                try { setPlan(await rowynDevelopmentPresetPreview(item.id)); }
                catch (error) { setPlan(null); setMessage(String(error)); }
              })}>
              Preview {item.label}
            </button>
          ))}
        </div>
        {plan && preset === plan.preset && (
          <section aria-label="Package installation plan">
            <h2>Install {presetLabel(plan.preset)}</h2>
            <p>Target: <code>{plan.target}</code> (managed, digest-pinned Ubuntu 26.10 container)</p>
            <p>Packages: {plan.packages.map(pkg => <code key={pkg}>{pkg} </code>)}</p>
            <ol>{plan.commands.map((argv, index) => <li key={index}><code>{argv.join(' ')}</code></li>)}</ol>
            <p>Cancellation stops the process group where possible; packages and apt state may be partially changed and are not rolled back.</p>
            <button className="action-button" disabled={lockedActions} onClick={() => {
              setPending({ kind: 'preset', preset: plan.preset, target: plan.target, packages: plan.packages });
            }}>Install this preset</button>
          </section>
        )}
        <div className="home-actions">
          <button className="action-button" disabled={busy} onClick={() => void act(() => controller.current?.refresh() ?? Promise.resolve())}>
            Refresh status
          </button>
          <button
            className="action-button"
            disabled={lockedActions || !caps.canCreate}
            onClick={() => setPending({ kind: 'create' })}
          >
            Create Ubuntu environment
          </button>
          <button
            className="action-button"
            disabled={lockedActions || !caps.canOpen}
            onClick={() => void act(() => controller.current?.open(rowynDevelopmentOpen) ?? Promise.resolve())}
          >
            Open terminal
          </button>
          {job?.status === 'running' && (
            <button className="action-button" disabled={busy} onClick={() => void act(() => controller.current?.resumePolling() ?? Promise.resolve())}>
              Resume status
            </button>
          )}
        </div>
        {pending && (
          <section className="glass dashboard-card" role="alertdialog" aria-labelledby="rowyn-development-confirm">
            <h2 id="rowyn-development-confirm">{pendingTitle(pending)}</h2>
            <p className="card-copy">{pendingCopy(pending)}</p>
            <div className="home-actions">
              <button className="action-button" disabled={lockedActions} onClick={() => setPending(null)}>Cancel</button>
              <button className="action-button" disabled={lockedActions} onClick={() => void confirmPending()}>
                {pendingConfirmLabel(pending)}
              </button>
            </div>
          </section>
        )}
        {caps.canDelete && (
          <>
            <p className="card-copy">{DELETE_WARNING}</p>
            <label>
              Type <code>{DEVELOPMENT_NAME}</code> to confirm deletion
              <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
            </label>
            <button
              className="action-button"
              disabled={lockedActions || !!deleteConfirmation(confirmation)}
              onClick={() => setPending({ kind: 'delete' })}
            >
              Delete environment
            </button>
          </>
        )}
        {running && (
          <button className="action-button" disabled={busy} onClick={() => void act(() => controller.current?.cancel() ?? Promise.resolve())}>
            Cancel operation
          </button>
        )}
        <p className="rowyn-state">{status?.status ? `Environment ${status.status}` : 'Environment status unavailable'}</p>
        {status?.name && <p className="card-copy">Container: {status.name}{status.image ? ` · ${status.image}` : ''}</p>}
        <p className="card-copy" role="status">{message}</p>
        {job && <p className="rowyn-state">Job {job.status}: {job.detail}</p>}
      </section>
    </div>
  );
}
