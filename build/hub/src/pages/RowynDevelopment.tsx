import { useEffect, useRef, useState } from 'react';
import { confirmUserAction } from '../services/liveData';
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

export function Development() {
  useSectionFollow(SECTIONS);
  const [status, setStatus] = useState<PageState | null>(null);
  const [message, setMessage] = useState('Refresh status to inspect the dedicated environment.');
  const [job, setJob] = useState<DevelopmentJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [preset, setPreset] = useState<string | null>(null);
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

  return (
    <div id="hub-section-development" className="page-content rowyn-mvp">
      <section className="glass dashboard-card">
        <span className="home-eyebrow">Rowyn Development</span>
        <h1>Ubuntu development environment</h1>
        <p className="card-copy">
          Creates one dedicated Distrobox named <code>{DEVELOPMENT_NAME}</code> from{' '}
          <code>{DEVELOPMENT_IMAGE_LABEL}</code>. Developer tools stay in the container rather than the immutable
          host OS. Distrobox shares your home directory and is <strong>not a sandbox</strong>; software
          inside can access your home files.
        </p>
        <p className="card-copy">
          This page only manages that fixed environment. It does not accept shell commands or create
          arbitrary containers. Missing tooling and permission errors are reported as unavailable, not as
          absence. The first terminal open may initialize the container and download packages.
        </p>
        <p className="card-copy">
          Creating the container installs the fixed APT (USN-8996-1) inside it, and every preset
          refreshes apt again before it installs packages, so the shared home directory is never
          served by a package client carrying CVE-2026-109518.
        </p>
        <p className="card-copy">
          Cancelling stops the operation where possible; it does not roll back packages or files already
          changed. Your home directory is shared with the container.
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
            <h2>Install {DEVELOPMENT_PRESETS.find(item => item.id === plan.preset)?.label}</h2>
            <p>Target: <code>{plan.target}</code> (managed, digest-pinned Ubuntu 26.10 container)</p>
            <p>Packages: {plan.packages.map(pkg => <code key={pkg}>{pkg} </code>)}</p>
            <ol>{plan.commands.map((argv, index) => <li key={index}><code>{argv.join(' ')}</code></li>)}</ol>
            <p>Cancellation stops the process group where possible; packages and apt state may be partially changed and are not rolled back.</p>
            <button className="action-button" disabled={lockedActions} onClick={() => {
              if (confirmUserAction(`Install ${plan.packages.join(', ')} in ${plan.target}? Package and apt state may be partially changed if cancelled.`)) {
                const operation = presetOperation(plan.preset);
                if (operation) void act(() => controller.current?.start(operation, `install:${plan.preset}`) ?? Promise.resolve());
              }
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
            onClick={() => {
              if (confirmUserAction(`Create the dedicated ${DEVELOPMENT_NAME} Ubuntu environment?`)) {
                void act(() => controller.current?.start('create') ?? Promise.resolve());
              }
            }}
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
              onClick={() => {
                if (confirmUserAction(`Delete ${DEVELOPMENT_NAME}? ${DELETE_WARNING}`)) {
                  void act(async () => {
                    await controller.current?.start('delete', confirmation);
                    if (mounted.current && controller.current?.job?.status !== 'failed') setConfirmation('');
                  });
                }
              }}
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
