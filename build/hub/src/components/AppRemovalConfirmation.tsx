import { useEffect, useRef, useState } from 'react';

export function canConfirmRemoval(acknowledged: boolean, busy: boolean, appId: string): boolean {
  return acknowledged && !busy && /^[A-Za-z0-9_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){2,}$/.test(appId);
}

export function AppRemovalConfirmation({app, busy, onCancel, onConfirm}: {
  app: {id: string; name: string}; busy: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    panel.current?.scrollIntoView({block: 'center'});
    panel.current?.focus();
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  const allowed = canConfirmRemoval(acknowledged, busy, app.id);
  return <section ref={panel} tabIndex={-1} className="glass dashboard-card" role="alertdialog" aria-labelledby="rowyn-removal-heading" aria-describedby="rowyn-removal-detail">
    <h3 id="rowyn-removal-heading">Review app removal</h3>
    <p><strong>{app.name || app.id}</strong> · <code>{app.id}</code></p>
    <p id="rowyn-removal-detail">The existing Apps service chooses the installed scope; system-wide removal affects other users. If the same app exists in both scopes, the system copy is selected first. Review both installations before continuing. Reported installed size is not a guaranteed amount of reclaimed space: runtimes and objects can be shared. This removes the application, not its saved application data or your personal files.</p>
    <label><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} /> I have reviewed the app identity and removal scope.</label>
    <div className="home-actions">
      <button className="action-button" disabled={busy} onClick={onCancel}>Keep app</button>
      <button className="action-button" disabled={!allowed} onClick={() => { if (allowed) onConfirm(); }}>Confirm removal</button>
    </div>
  </section>;
}
