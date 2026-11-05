import { useEffect, useRef, useState } from 'react';
import type { BackupSnapshot } from '../services/rowynBackupModel';
import { backupSummary, backupStatus } from '../services/rowynBackupModel';
import { fetchRowynBackupsSnapshot, openRowynSettings } from '../services/rowynSettings';
import '../styles/rowynMvp.css';

export function Backups() {
  const [snapshot, setSnapshot] = useState<BackupSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('Not checked yet. Refresh to read Kup configuration.');
  const mounted = useRef(false);
  const locked = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current++; };
  }, []);

  async function run(action: 'refresh' | 'settings') {
    if (locked.current) return;
    locked.current = true;
    const token = ++generation.current;
    setBusy(true);
    setStatus(action === 'refresh' ? 'Reading Kup configuration…' : 'Opening Kup settings…');
    if (action === 'refresh') setSnapshot(null);
    try {
      if (action === 'refresh') {
        const next = await fetchRowynBackupsSnapshot();
        if (mounted.current && token === generation.current) { setSnapshot(next); setStatus(backupSummary(next)); }
      } else {
        const result = await openRowynSettings('backups');
        if (mounted.current && token === generation.current) setStatus(result);
      }
    } catch (error) {
      if (mounted.current && token === generation.current) setStatus(`Unavailable: ${String(error)}`);
    } finally {
      locked.current = false;
      if (mounted.current && token === generation.current) setBusy(false);
    }
  }

  return <div id="hub-section-backups" className="page-content rowyn-mvp">
    <section className="glass dashboard-card"><span className="home-eyebrow">Rowyn Backups</span><h1>Backups</h1>
      <p className="card-copy">Kup configuration is read only; Rowyn does not run backups or verify destination connectivity or backup contents. Versioned backups retain history; mirrors do not. A backup on the same disk does not protect against disk failure. OS rollback does not restore personal files.</p>
      <div className="home-actions">
        <button className="action-button action-button-primary" onClick={() => void run('refresh')} disabled={busy}>{busy ? 'Working…' : 'Refresh Kup status'}</button>
        <button className="action-button" onClick={() => void run('settings')} disabled={busy}>Open Kup settings</button>
      </div>
      <p role="status" className="card-copy">{status}</p>
    </section>
    {snapshot?.plans.map((plan, index) => <section className="glass dashboard-card" key={`${plan.name}-${index}`}>
      <h2>{plan.name}</h2><p className="card-copy">Status: {backupStatus(plan)} · Type: {plan.kind === 'versioned' ? 'Versioned' : 'Mirror'}</p>
      <p className="card-copy">Destination: {plan.destination || 'Not configured'}</p>
      <p className="card-copy">Included: {plan.defaultIncluded ? 'Kup defaults apply; open settings to review included files' : plan.included.length ? plan.included.join(', ') : 'No paths listed'}</p>
      <p className="card-copy">Excluded: {plan.excluded.length ? plan.excluded.join(', ') : 'None listed'}</p>
      <p className="card-copy">Last completed backup recorded by Kup: {plan.lastCompleted ?? 'Not reported'} (not independently verified)</p>
    </section>)}
  </div>;
}
