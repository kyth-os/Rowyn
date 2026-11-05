import { useEffect, useRef, useState } from "react";
import { fetchRecoverySnapshot, fetchRecoveryHistory } from "../services/rowynRecovery";
import { digestMatchedNotes, type RecoverySnapshot, type DeploymentIdentity } from "../services/rowynRecoveryModel";

const DEADLINE_MS = 12_000;
async function deadline<T>(work: () => Promise<T>): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([work(), new Promise<null>((resolve) => { timer = setTimeout(() => resolve(null), DEADLINE_MS); })]);
  } catch { return null; } finally { if (timer) clearTimeout(timer); }
}

type Slot = "booted" | "staged" | "rollback";

/** Cached deployment identities and their published notes. Read-only. */
export function RecoveryClarity() {
  const [snapshot, setSnapshot] = useState<RecoverySnapshot | null>(null);
  const [selected, setSelected] = useState<{ slot: Slot; digest: string } | null>(null);
  const [history, setHistory] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Cached deployment status has not been read yet.");
  const mounted = useRef(false), lock = useRef(false), generation = useRef(0);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);

  const run = async (action: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    const id = ++generation.current;
    setBusy(true);
    try { await action(); }
    catch { if (mounted.current && generation.current === id) setMessage("The requested read failed; cached information remains unchanged."); }
    finally { lock.current = false; if (mounted.current && generation.current === id) setBusy(false); }
  };

  const refresh = () => run(async () => {
    setMessage("Reading cached deployment status…");
    const result = await deadline(fetchRecoverySnapshot);
    if (!mounted.current) return;
    setSnapshot(result);
    setSelected(null);
    setHistory(null);
    setMessage(result ? "Read-only cached status; this is not a live health check." : "Cached deployment status is unavailable or malformed.");
  });

  const choose = (slot: Slot, item: DeploymentIdentity | null) => {
    if (!item?.digest || lock.current) return;
    setSelected({ slot, digest: item.digest });
    setHistory(null);
    setMessage("Selected deployment identity; release notes have not been requested.");
  };

  const loadNotes = () => run(async () => {
    if (!selected) return;
    setHistory(null);
    setMessage("Loading published history for this selected deployment digest…");
    const result = await deadline(fetchRecoveryHistory);
    if (!mounted.current) return;
    setHistory(result);
    const matched = digestMatchedNotes(selected.digest, result);
    setMessage(matched.matched ? "Published history entry matches the selected deployment digest." : result === null ? "Release history is unavailable." : "No published history entry matches this deployment digest.");
  });

  const slots: [Slot, string, DeploymentIdentity | null][] = [
    ["booted", "Booted — running now", snapshot?.booted ?? null],
    ["staged", "Staged — next-boot candidate", snapshot?.staged ?? null],
    ["rollback", "Rollback — previous OS deployment", snapshot?.rollback ?? null],
  ];
  const matched = selected ? digestMatchedNotes(selected.digest, history) : null;

  return <section id="hub-section-recovery" className="glass dashboard-card" aria-labelledby="recovery-clarity-title">
    <header>
      <h2 id="recovery-clarity-title">Deployments &amp; release notes</h2>
      <p>Read-only. Refreshing reads cached status; nothing here updates, stages, reboots, or rolls back.</p>
    </header>
    <div className="rowyn-inline-actions">
      <button type="button" disabled={busy} onClick={() => void refresh()} className="action-button">Refresh cached status</button>
      <button type="button" disabled={busy || !selected} className="action-button action-button-primary" onClick={() => void loadNotes()}>Show notes for selected deployment</button>
    </div>
    <p role="status">{message}</p>
    {snapshot ? <dl className="rowyn-recovery-slots">
      {slots.map(([key, label, item]) => <div key={key}>
        <dt>{label}</dt>
        <dd>{item?.image ?? item?.version ?? (item ? "Identity unavailable" : key === "staged" ? "No staged candidate reported" : "Unknown")}</dd>
        <dd>{item?.version ?? "Version unavailable"}</dd>
        <dd>{item?.timestamp ?? "Timestamp unavailable"}</dd>
        <dd className="break-all">{item?.digest ?? "Digest unavailable"}</dd>
        <button type="button" disabled={busy || !item?.digest} className="action-button" onClick={() => choose(key, item)}>{selected?.slot === key ? "Selected deployment" : "Use this deployment"}</button>
      </div>)}
    </dl> : null}
    {matched?.matched ? <div>
      <h3>{matched.title ?? "Release notes"} — {matched.version ?? "version unavailable"}</h3>
      {matched.published_at && <p>{matched.published_at}</p>}
      {matched.highlights.length ? <ul>{matched.highlights.map((item, i) => <li key={`${i}-${item}`}>{item}</li>)}</ul> : <p>No highlights supplied.</p>}
    </div> : selected && history !== null ? <p>Release notes unavailable: no exact digest match was returned.</p> : null}
    <p className="rowyn-note">OS rollback is not a personal-file or app-data restore; use <a href="#/backups">Backups</a> for that.</p>
  </section>;
}
export default RecoveryClarity;
