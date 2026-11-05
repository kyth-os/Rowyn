import { useEffect, useRef, useState } from 'react';
import { rowynPowerSnapshot, rowynStorageSnapshot } from '../services/rowynCare';
import { openRowynSettings } from '../services/rowynSettings';
import type { PowerSnapshot, StorageSnapshot } from '../services/rowynCareModel';

function useRead<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const lock = useRef(false), mounted = useRef(false), generation = useRef(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);
  const refresh = async () => {
    if (lock.current) return;
    lock.current = true;
    const id = ++generation.current;
    setData(null); setBusy(true); setError('');
    try { const value = await load(); if (mounted.current && id === generation.current) setData(value); }
    catch (e) { if (mounted.current && id === generation.current) setError(String(e)); }
    finally { lock.current = false; if (mounted.current && id === generation.current) setBusy(false); }
  };
  return { data, error, busy, refresh };
}

function bytes(n: number | null) {
  if (n === null) return 'unknown';
  if (n < 1000) return `${n} B`;
  const units = ['kB', 'MB', 'GB', 'TB'];
  let v = n / 1000, i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(1)} ${units[i]}`;
}

function stateLabel(s: string) {
  return s === 'available' ? 'reported' : s === 'unsupported' ? 'unsupported' : 'unavailable';
}

function meter(percent: number | null) {
  return <div className="rowyn-meter" aria-hidden="true"><span style={{ width: `${percent ?? 0}%` }} /></div>;
}

export function StorageCare() {
  const s = useRead<StorageSnapshot>(rowynStorageSnapshot);
  return <section id="hub-section-storage" className="rowyn-diag-group">
    <header className="rowyn-diag-section-head">
      <h2>Storage care</h2>
      <button className="action-button" disabled={s.busy} onClick={() => void s.refresh()}>{s.busy ? 'Refreshing…' : 'Refresh'}</button>
    </header>
    {s.error && <p role="alert" className="rowyn-note">Unavailable: {s.error}</p>}
    {!s.data && !s.error && <p className="rowyn-note">Read-only capacity snapshot. Nothing is deleted, moved, or changed by reading it.</p>}
    {s.data && <>
      <p className="rowyn-note">{s.data.state === 'unavailable' ? 'Storage snapshot unavailable.' : s.data.state === 'partial' ? 'Some storage sources are unavailable; this is not a complete health assessment.' : 'Current read-only snapshot.'}</p>
      <div className="rowyn-diag-grid">
        {s.data.volumes.map((v, i) => <article className="rowyn-diag-card" key={`${v.name}-${i}`}>
          <header><h3>{v.name}</h3><span className="rowyn-state">{v.usagePercent === null ? 'usage unknown' : `${v.usagePercent.toFixed(1)}% used`}</span></header>
          <p>{bytes(v.freeBytes)} free of {bytes(v.totalBytes)}</p>
          {meter(v.usagePercent)}
        </article>)}
        {s.data.inventory.map((v, i) => <article className="rowyn-diag-card" key={`${v.category}-${i}`}>
          <header><h3>{v.category}</h3><span className="rowyn-state">{v.count === null ? 'count unknown' : `${v.count} reported`}</span></header>
          <p>{v.detail ?? stateLabel(v.status)}{v.estimatedBytes !== null ? ` · ${bytes(v.estimatedBytes)} aggregate estimate` : ''}</p>
          {v.entries?.length ? <details className="rowyn-diag-raw">
            <summary>{v.entries.length} installed {v.entries.length === 1 ? 'entry' : 'entries'}</summary>
            <ul>{v.entries.map((e, j) => <li key={`${e.reference}-${e.scope}-${j}`}>{e.name} · {e.scope} · installed size {e.installedSize ?? 'unknown'}</li>)}</ul>
          </details> : null}
        </article>)}
      </div>
      <p className="rowyn-note">Inventory sizes are per-app estimates; shared objects and Btrfs can make them differ from reclaimable space. Immutable deployments and composefs are not physical capacity measures; /var/home may share the same backing filesystem, so do not add these figures. Review candidates in <a href="#/apps">Apps</a> or <a href="#/development">Development</a>. Personal files, deployments, and shared objects are never automatically deleted.</p>
    </>}
  </section>;
}

export function PowerCare() {
  const p = useRead<PowerSnapshot>(rowynPowerSnapshot);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsMessage, setSettingsMessage] = useState('');
  const lock = useRef(false), mounted = useRef(false), generation = useRef(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);
  const settings = async () => {
    if (lock.current) return;
    lock.current = true;
    const id = ++generation.current;
    setSettingsBusy(true); setSettingsMessage('');
    try { await openRowynSettings('power'); if (mounted.current && id === generation.current) setSettingsMessage('Settings launch requested.'); }
    catch (e) { if (mounted.current && id === generation.current) setSettingsMessage(`Could not open power settings: ${String(e)}`); }
    finally { lock.current = false; if (mounted.current && id === generation.current) setSettingsBusy(false); }
  };
  const unknown = p.data?.state === 'unavailable' || p.data?.state === 'partial';
  return <section id="hub-section-power" className="rowyn-diag-group">
    <header className="rowyn-diag-section-head">
      <h2>Power care</h2>
      <div className="rowyn-inline-actions">
        <button className="action-button" disabled={p.busy} onClick={() => void p.refresh()}>{p.busy ? 'Refreshing…' : 'Refresh'}</button>
        <button className="action-button" disabled={settingsBusy} onClick={() => void settings()}>Power settings</button>
      </div>
    </header>
    {p.error && <p role="alert" className="rowyn-note">Unavailable: {p.error}</p>}
    {settingsMessage && <p className="rowyn-note" role="status">{settingsMessage}</p>}
    {!p.data && !p.error && <p className="rowyn-note">Read-only power snapshot. Nothing is changed by reading it.</p>}
    {p.data && <>
      <p className="rowyn-note">{p.data.state === 'unavailable' ? 'Power snapshot unavailable.' : p.data.state === 'partial' ? 'Some power sources are unavailable.' : 'Current read-only snapshot.'}</p>
      <div className="rowyn-diag-grid">
        {p.data.batteries.length ? p.data.batteries.map((b, i) => <article className="rowyn-diag-card" key={i}>
          <header><h3>Battery {i + 1}</h3><span className="rowyn-state">{b.capacity === null ? 'capacity unknown' : `${b.capacity}%`}</span></header>
          <p>{b.status ?? 'status unknown'} · {b.healthPercent === null ? 'health unknown' : `${b.healthPercent.toFixed(1)}% health`}</p>
          {meter(b.capacity)}
        </article>) : <article className="rowyn-diag-card">
          <header><h3>Battery</h3><span className="rowyn-state">{unknown ? 'unavailable' : 'none reported'}</span></header>
          <p>{unknown ? 'Battery presence not fully determined; available power sources may be incomplete.' : 'No battery reported by the available power sources.'}</p>
        </article>}
        <article className="rowyn-diag-card">
          <header><h3>Charge limit</h3><span className="rowyn-state">{p.data.chargeLimit.value === null ? stateLabel(p.data.chargeLimit.state) : `${p.data.chargeLimit.value}%`}</span></header>
          <p>{p.data.chargeLimit.value === null ? 'No charge limit reported.' : 'Maximum charge the firmware will hold.'}</p>
        </article>
        <article className="rowyn-diag-card">
          <header><h3>Power profile</h3><span className="rowyn-state">{p.data.profile.value ?? stateLabel(p.data.profile.state)}</span></header>
          <p>{p.data.profile.value === null ? 'No active power profile reported.' : 'Active platform profile.'}</p>
        </article>
        <article className="rowyn-diag-card">
          <header><h3>Kernel capabilities</h3><span className="rowyn-state">{stateLabel(p.data.capabilities.state)}</span></header>
          <p>{p.data.capabilities.state === 'available' ? (p.data.capabilities.value.length ? p.data.capabilities.value.join(', ') : 'No capabilities reported.') : 'Kernel capability list unavailable.'}</p>
        </article>
      </div>
    </>}
  </section>;
}
