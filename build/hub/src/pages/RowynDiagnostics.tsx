import { useRef, useState } from 'react';
import { rowynDiagnosticsSnapshot, exportRowynReport } from '../services/rowynMvp';
import { formatReport, type DiagnosticsSnapshot } from '../services/rowynMvpModel';
import '../styles/rowynMvp.css';

export function Diagnostics() {
  const [snapshot, setSnapshot] = useState<DiagnosticsSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('Not checked yet. Choose Refresh checks to read this system.');
  const lock = useRef(false);
  const report = snapshot ? formatReport(snapshot) : '';
  async function run(action: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try { setStatus(await action()); }
    catch (error) { setStatus(`Unavailable: ${error instanceof Error ? error.message : String(error)}`); }
    finally { lock.current = false; setBusy(false); }
  }
  return <div className="page-content rowyn-mvp">
    <section className="glass dashboard-card">
      <span className="home-eyebrow">Rowyn system tools</span><h1>Diagnostics</h1>
      <p className="card-copy">Read-only, local checks. Nothing is changed or uploaded. Checks run only when requested; missing tools and denied reads are shown, not treated as healthy.</p>
      <div className="home-actions">
        <button className="action-button action-button-primary" disabled={busy} onClick={() => run(async () => {
          setSnapshot(null); setStatus('Reading system checks…');
          const next = await rowynDiagnosticsSnapshot(); setSnapshot(next);
          return 'Checks finished. Review each result below.';
        })}>{busy ? 'Working…' : 'Refresh checks'}</button>
        <button className="action-button" disabled={busy || !snapshot} onClick={() => run(async () => {
          await navigator.clipboard.writeText(report); return 'Report copied. Review before sharing.';
        })}>Copy report</button>
        <button className="action-button" disabled={busy || !snapshot} onClick={() => run(() => exportRowynReport(report))}>Save report to Downloads</button>
      </div>
      <p role="status" className="card-copy">{status}</p>
      {snapshot && <p className="card-copy">Collected {new Date(snapshot.collected_at * 1000).toLocaleString()}. This snapshot is not live.</p>}
    </section>
    {snapshot?.readings.map((reading) => <section key={reading.name} className="glass dashboard-card">
      <h2>{reading.name} <span className="rowyn-state">{reading.state}</span></h2><pre>{reading.detail}</pre>
    </section>)}
    <section className="glass dashboard-card"><h2>Report preview</h2>
      <p className="card-copy">Redaction is best effort, not a privacy guarantee. Review this exact preview before copying or saving. Saving creates a new private text file in Downloads only after your click; no background files are written.</p>
      <pre aria-label="Report preview">{report || 'No report yet.'}</pre>
    </section>
  </div>;
}
