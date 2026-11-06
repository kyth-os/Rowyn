import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { rowynReadinessSnapshot } from '../services/rowynReadiness';
import { openRowynSettings } from '../services/rowynSettings';
import { readinessTargets, type ReadinessId, type ReadinessSnapshot, type ReadinessState } from '../services/rowynReadinessModel';

const stateLabels: Record<ReadinessState, string> = {
  ready: 'Ready', 'needs-setup': 'Needs setup', attention: 'Needs attention', unavailable: 'Unavailable',
};

export function ReadinessResults({ snapshot, busy, onAction }: {
  snapshot: ReadinessSnapshot; busy: boolean; onAction: (id: ReadinessId) => void;
}) {
  const checked = new Date(snapshot.checkedAt * 1000);
  return <>
    <p className="rowyn-note">Checked <time dateTime={checked.toISOString()}>{checked.toLocaleString()}</time>. Readiness is a local signal, not a test of hardware, restore, or update success.</p>
    <div className="rowyn-readiness-grid">{snapshot.checks.map((check) => (
      <article className="rowyn-readiness-card" key={check.id}>
        <header><h3>{check.title}</h3><span className={`rowyn-state readiness-${check.state}`}>{stateLabels[check.state]}</span></header>
        <p>{check.detail}</p>
        {check.id === 'updates' && check.updateAttempt && <dl className="rowyn-update-history">
          <dt>Last automatic attempt</dt><dd><time dateTime={new Date(check.updateAttempt.startedAt * 1000).toISOString()}>{new Date(check.updateAttempt.startedAt * 1000).toLocaleString()} · {check.updateAttempt.outcome.replace(/_/g, ' ')}</time></dd>
          <dt>Last successful check</dt><dd>{check.updateAttempt.lastSuccessAt === null ? 'None recorded' : new Date(check.updateAttempt.lastSuccessAt * 1000).toLocaleString()}</dd>
          {check.updateAttempt.lastStagedAt !== null && <><dt>Last staged update</dt><dd>{new Date(check.updateAttempt.lastStagedAt * 1000).toLocaleString()}</dd></>}
          {check.updateAttempt.reasonCode === 'runtime_auth_io' && <dd className="rowyn-update-history-note">The updater could not read runtime authentication metadata. The prior success does not mean this attempt succeeded; credential contents were not inspected.</dd>}
        </dl>}
        <button type="button" className="action-button" disabled={busy} onClick={() => onAction(check.id)}>{readinessTargets[check.id].label}</button>
      </article>
    ))}</div>
  </>;
}

export function RowynReadiness() {
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<ReadinessSnapshot | null>(null);
  const [status, setStatus] = useState('Not checked yet. Choose Check readiness to read this system.');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; lock.current = false; };
  }, []);

  async function run(id?: ReadinessId) {
    if (lock.current || !mounted.current) return;
    lock.current = true;
    const token = ++generation.current;
    const current = () => mounted.current && generation.current === token;
    setBusy(true);
    if (!id) { setSnapshot(null); setStatus('Checking readiness…'); }
    else { setStatus('Opening the selected tool…'); }
    try {
      if (!id) {
        const next = await rowynReadinessSnapshot();
        if (current()) { setSnapshot(next); setStatus('Readiness checks finished. Review any setup, attention, or unavailable states below.'); }
      } else {
        const action = readinessTargets[id];
        if ('route' in action) { navigate(action.route); }
        else {
          const result = await openRowynSettings(action.setting);
          if (current()) setStatus(result);
        }
      }
    } catch (error) {
      if (current()) setStatus(`Unavailable: ${error instanceof Error ? error.message : 'The request could not be completed.'}`);
    } finally {
      if (current()) { lock.current = false; setBusy(false); }
    }
  }

  return <section id="hub-section-readiness" className="glass dashboard-card rowyn-readiness" aria-labelledby="rowyn-readiness-title" aria-busy={busy}>
    <div className="rowyn-readiness-heading">
      <div><span className="home-eyebrow">BEFORE YOUR NEXT STEP</span><h2 id="rowyn-readiness-title">System readiness</h2></div>
      <button type="button" className="action-button action-button-primary" disabled={busy} onClick={() => void run()}>{busy ? 'Working…' : 'Check readiness'}</button>
    </div>
    <p className="card-copy">No checks run until you choose the button. These bounded local reads do not change settings, scan the network, request administrator access, or repair services.</p>
    <p role="status" className="rowyn-note">{status}</p>
    {snapshot && <ReadinessResults snapshot={snapshot} busy={busy} onAction={(id) => void run(id)} />}
  </section>;
}
