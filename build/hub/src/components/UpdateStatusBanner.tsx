import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchUpdatesSnapshot, invalidateSharedReads, type UpdatesSnapshot } from '../services/liveData';
import { projectUpdateBanner, type UpdateBannerState } from '../services/rowynUpdateStatusModel';
import { reportStatusSource } from '../services/rowynStatusSources';

// States only a readable bootc can produce. A failed read reports "blocked",
// so that state must not count as the source existing on this host.
const UPDATE_SOURCE_STATES: UpdateBannerState[] = ['current', 'available', 'staged', 'restartready', 'staging'];

/** Passive read model shared by Home and Updates. No reboot or staging actions. */
export function UpdateStatusBanner() {
  const [snapshot, setSnapshot] = useState<UpdatesSnapshot | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const mounted = useRef(false);
  const generation = useRef(0);

  async function refresh() {
    const token = ++generation.current;
    setLoading(true);
    try {
      invalidateSharedReads('updates-snapshot');
      const next = await fetchUpdatesSnapshot();
      if (!mounted.current || token !== generation.current) return;
      setSnapshot(next);
      setFailed(false);
    } catch {
      if (!mounted.current || token !== generation.current) return;
      setSnapshot(null);
      setFailed(true);
    } finally {
      if (mounted.current && token === generation.current) setLoading(false);
    }
  }

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; generation.current++; };
  }, []);
  const view = projectUpdateBanner(snapshot, failed);
  useEffect(() => {
    reportStatusSource('updates', UPDATE_SOURCE_STATES.includes(view.state));
  }, [view.state]);
  return <section aria-label="System update status" data-state={view.state} className="glass dashboard-card rowyn-update-status-banner">
    <strong>{view.label}</strong><p className="card-copy">{view.guidance}</p>
    <div className="home-actions">
      <button className="action-button" type="button" onClick={() => void refresh()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh status'}</button>
      <Link to="/updates">Review Updates</Link>
    </div>
  </section>;
}
