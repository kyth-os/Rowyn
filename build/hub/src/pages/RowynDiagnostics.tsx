import { useRef, useState } from 'react';
import { StorageCare, PowerCare } from '../components/SystemCare';
import { RowynReadiness } from '../components/RowynReadiness';
import { rowynDiagnosticsSnapshot, exportRowynReport } from '../services/rowynMvp';
import { formatReport, readingNeedsAttention, readingStateLabel, readingTone, type DiagnosticsSnapshot, type Reading } from '../services/rowynMvpModel';
import { openRowynSettings, type SettingTarget } from '../services/rowynSettings';
import { copyText } from '../services/rowynClipboard';
import { useSectionFollow, type SectionMap } from '../hooks/useSectionFollow';
import '../styles/rowynMvp.css';

const SECTIONS: SectionMap = {
  Diagnostics: 'hub-section-diagnostics',
  Readiness: 'hub-section-readiness',
  Storage: 'hub-section-storage',
  Power: 'hub-section-power',
};

const HARDWARE: [string, string, SettingTarget][] = [
  ['Bluetooth', 'Bluetooth', 'bluetooth'],
  ['Wi-Fi', 'Wi-Fi', 'wifi'],
  ['Audio', 'Audio', 'audio'],
  ['Displays', 'Displays', 'displays'],
];

// A card carries the headline; the probe's raw text stays available but folded,
// so a wall of command output never has to be read to see what is wrong.
function firstLine(detail: string) {
  const line = detail.split('\n').map((entry) => entry.trim()).find(Boolean) ?? 'No detail reported.';
  return line.length > 120 ? `${line.slice(0, 117).trimEnd()}…` : line;
}

export function Diagnostics() {
  useSectionFollow(SECTIONS);
  const [snapshot, setSnapshot] = useState<DiagnosticsSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('Not checked yet. Choose Refresh checks to read this system.');
  const lock = useRef(false);
  const report = snapshot ? formatReport(snapshot) : '';
  const readings = snapshot?.readings ?? [];
  const attention = readings.filter((reading) => readingNeedsAttention(reading.state)).length;

  async function run(action: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try { setStatus(await action()); }
    catch (error) { setStatus(`Unavailable: ${error instanceof Error ? error.message : String(error)}`); }
    finally { lock.current = false; setBusy(false); }
  }

  function checkCard(reading: Reading) {
    const hardware = HARDWARE.find(([name]) => name === reading.name);
    return <article key={reading.name} className="rowyn-diag-card">
      <header>
        <h3>{hardware?.[1] ?? reading.name}</h3>
        <span className={`rowyn-state ${readingTone(reading.state)}`}>{readingStateLabel(reading.state)}</span>
      </header>
      <p>{firstLine(reading.detail)}</p>
      <details className="rowyn-diag-raw">
        <summary>Raw output</summary>
        <pre>{reading.detail}</pre>
      </details>
      {hardware && (
        <button className="action-button" disabled={busy} onClick={() => run(() => openRowynSettings(hardware[2]))}>
          Open {hardware[1]} settings
        </button>
      )}
    </article>;
  }

  return <div className="page-content rowyn-mvp rowyn-diagnostics">
    <section id="hub-section-diagnostics" className="glass dashboard-card rowyn-diag-head">
      <div>
        <span className="home-eyebrow">Rowyn system tools</span>
        <h1>Diagnostics</h1>
        <p className="card-copy">Read-only local checks. Nothing is changed or uploaded, and a check that cannot run is reported as unavailable rather than healthy.</p>
      </div>
      <div className="rowyn-inline-actions">
        <button className="action-button action-button-primary" disabled={busy} onClick={() => run(async () => {
          setSnapshot(null); setStatus('Reading system checks…');
          const next = await rowynDiagnosticsSnapshot(); setSnapshot(next);
          return 'Checks finished.';
        })}>{busy ? 'Working…' : 'Refresh checks'}</button>
        <button className="action-button" disabled={busy || !snapshot} onClick={() => run(() => exportRowynReport(report))}>Save report to Downloads</button>
        <button className="action-button" disabled={busy || !snapshot} onClick={() => run(async () => {
          const result = await copyText(report);
          return result.ok ? 'Report copied. Review before sharing.' : result.reason;
        })}>Copy report</button>
      </div>
      <p role="status" className="rowyn-note">{status}</p>
    </section>
    {snapshot && <div className="rowyn-diag-stats">
      <article className="rowyn-stat"><span>Checks run</span><strong>{readings.length}</strong></article>
      <article className="rowyn-stat"><span>Needs attention</span><strong>{attention}</strong></article>
      <article className="rowyn-stat"><span>Collected</span><strong>{new Date(snapshot.collected_at * 1000).toLocaleTimeString()}</strong></article>
    </div>}
    {snapshot && <section className="rowyn-diag-group" aria-label="System checks">
      <h2>System checks</h2>
      <p className="rowyn-note">Read-only signals, not a test of device function. Opening settings only opens the desktop panel; it does not change hardware state.</p>
      {readings.length
        ? <div className="rowyn-diag-grid">{readings.map(checkCard)}</div>
        : <p className="rowyn-note">No checks reported a reading.</p>}
    </section>}
    <RowynReadiness />
    <StorageCare />
    <PowerCare />
    <section className="rowyn-diag-group" aria-label="Report preview">
      <h2>Report preview</h2>
      <p className="rowyn-note">Redaction is best effort, not a privacy guarantee. Review this text before sharing; saving writes one private file to Downloads, only when you press the button.</p>
      <details className="rowyn-diag-raw">
        <summary>{snapshot ? 'Show the report text' : 'No report yet'}</summary>
        <pre aria-label="Report preview">{report || 'No report yet.'}</pre>
      </details>
    </section>
  </div>;
}
