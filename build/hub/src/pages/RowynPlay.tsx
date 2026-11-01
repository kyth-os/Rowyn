import { useRef, useState } from 'react';
import { ExternalLink } from '../components/ExternalLink';
import { cancelInstall, fetchInstalledFlatpaks, installFlatpak, waitInstallJob, invalidateSharedReads, getInFlightJob, confirmUserAction, type InstalledFlatpaksResult } from '../services/liveData';
import { rowynControllerScan, openRowynLauncher } from '../services/rowynMvp';
import { LAUNCHERS, launcherState, installLauncher, createPlayStatusOwner, launchOptions, type Reading } from '../services/rowynMvpModel';
import '../styles/rowynMvp.css';

export function Play() {
  const [scan, setScan] = useState<InstalledFlatpaksResult | null>(null);
  const [controllers, setControllers] = useState<Reading[] | null>(null);
  const [status, setStatus] = useState('Choose Refresh launchers to detect installed Flatpaks. No checks run automatically.');
  const [busy, setBusy] = useState(false);
  const [activeInstall, setActiveInstall] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [boundary, setBoundary] = useState<'flatpak' | 'host'>('flatpak');
  const [game, setGame] = useState('');
  const [gaming, setGaming] = useState(true);
  const [mimalloc, setMimalloc] = useState(false);
  const lock = useRef(false);
  const statusOwner = useRef(createPlayStatusOwner(setStatus));
  const options = launchOptions(boundary, gaming, mimalloc);
  const tracked = activeInstall ?? getInFlightJob('install');
  async function refreshLaunchers() {
    invalidateSharedReads('installed-flatpaks');
    const next = await fetchInstalledFlatpaks(); setScan(next);
    return !next ? 'Launcher scan unavailable. Use the installed Rowyn Hub and retry.' : next.failedScopes.length
      ? `Partial scan: ${next.failedScopes.join(', ')} unavailable. Unknown is not the same as not installed.` : 'Launcher scan finished.';
  }
  async function run(pending: string, action: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try { await statusOwner.current.run(pending, action); }
    finally { setActiveInstall(getInFlightJob('install') ?? null); lock.current = false; setBusy(false); }
  }
  return <div className="page-content rowyn-mvp">
    <section className="glass dashboard-card" id="hub-section-gaming">
      <span className="home-eyebrow">Rowyn Play · MVP</span><h1>Play</h1>
      <p className="card-copy">Four launchers, installed as personal Flatpaks from Flathub using the same jobs as Apps. No native package installs or system tuning. A launcher being installed does not establish game compatibility. This MVP detects launchers only; game-library discovery and play history are not implemented.</p>
      <div className="home-actions"><button className="action-button" disabled={busy} onClick={() => run('Reading installed launchers…', refreshLaunchers)}>Refresh launchers</button>
        {tracked && <button className="action-button" disabled={cancelBusy} onClick={async () => {
          setCancelBusy(true);
          try { await statusOwner.current.cancel(cancelInstall); }
          finally { setCancelBusy(false); }
        }}>Cancel install</button>}
        {tracked && !busy && <button className="action-button" onClick={() => run('Reconnecting to install…', () => waitInstallJob(tracked, 1800))}>Resume install status</button>}
      </div>
      <p className="card-copy" role="status">{status}</p>
      {busy && <p className="card-copy" role="progressbar" aria-label="Play action in progress">Working… Install jobs remain cancellable here or in Apps.</p>}
      <div className="rowyn-launchers">{LAUNCHERS.map((launcher) => {
        const state = launcherState(launcher.id, scan);
        return <article className="rowyn-launcher" key={launcher.id}><h2>{launcher.name}</h2>
          <p className="card-copy">{launcher.description}</p><p className="rowyn-state">{state}</p><code>{launcher.id}</code>
          <div className="home-actions"><button className="action-button" disabled={busy || state !== 'installed'} onClick={() => run(`Opening ${launcher.name}…`, () => openRowynLauncher(launcher.id))}>Open {launcher.name}</button>
          <button className="action-button" disabled={busy || state !== 'not installed' || !!tracked} onClick={() => {
            if (!confirmUserAction(`Install ${launcher.name} from Flathub for your user? This downloads software and may take several minutes.`)) return;
            void run(`Installing ${launcher.name}…`, async () => {
              const result = await installLauncher(launcher.id, { install: installFlatpak, wait: waitInstallJob }, setActiveInstall);
              setActiveInstall(null);
              const refreshed = await refreshLaunchers(); return `${result} ${refreshed}`;
            });
          }}>Install {launcher.name}</button></div>
        </article>;
      })}</div>
    </section>
    <section className="glass dashboard-card" id="hub-section-compatibility"><h2>Compatibility &amp; anti-cheat</h2>
      <p className="card-copy">Guidance reviewed 2026-10-31; not Rowyn-validated game results. Linux/Proton support changes with game, launcher and anti-cheat updates. Kernel-level Windows anti-cheat generally cannot run under Proton. Easy Anti-Cheat and BattlEye support requires the game developer to enable Linux/Proton support; the middleware name alone proves nothing. Check the exact title and multiplayer mode before buying or installing.</p>
      <p className="card-copy">Provenance: <ExternalLink href="https://partner.steamgames.com/doc/steamdeck/proton">Valve’s Proton / anti-cheat documentation</ExternalLink> (developer guidance), <ExternalLink href="https://www.protondb.com/">ProtonDB</ExternalLink> and <ExternalLink href="https://areweanticheatyet.com/">Are We Anti-Cheat Yet?</ExternalLink> (community reports, not guarantees). Links require a web browser; no compatibility lookup or network probe runs in this page.</p>
      <h3>Per-game launch options</h3>
      <label>Game label (local only; never executed)<input value={game} maxLength={80} onChange={(event) => setGame(event.target.value)} placeholder="Your game" /></label>
      <label>Where does this game run?<select value={boundary} onChange={(event) => setBoundary(event.target.value as 'flatpak' | 'host')}><option value="flatpak">Flatpak Steam / sandboxed launcher</option><option value="host">Host-native Steam / host command</option></select></label>
      <label><input type="checkbox" checked={gaming} disabled={boundary === 'flatpak'} onChange={(event) => setGaming(event.target.checked)} /> Use Rowyn gaming slice / GameMode when available</label>
      <label><input type="checkbox" checked={mimalloc} disabled={boundary === 'flatpak'} onChange={(event) => setMimalloc(event.target.checked)} /> Try mimalloc for this game only (experimental; may regress or warn on 32-bit games)</label>
      <p className="card-copy">Host wrappers /usr/bin/rowyn-game-launch and /usr/bin/rowyn-mimalloc-run are not generally accessible inside Flatpak Steam. Do not paste host paths there or use flatpak-spawn to escape the sandbox. The Flatpak-safe default below adds no host wrapper. The host-native example is for an already installed host launcher; this Hub does not install one. Undo by removing the launch options.</p>
      <p>{game || 'Selected game'} — paste into that game’s launch options:</p><pre aria-label="Launch options">{options}</pre>
      <button className="action-button" disabled={busy} onClick={() => run('Copying launch options…', async () => { await navigator.clipboard.writeText(options); return 'Launch options copied. No configuration was changed.'; })}>Copy launch options</button>
    </section>
    <section className="glass dashboard-card" id="hub-section-controllers"><h2>Controllers</h2>
      <p className="card-copy">Connect or pair a controller, then rescan. This reads the shipped controller checker only; it does not pair devices, load drivers, change permissions, or claim input testing passed.</p>
      <button className="action-button" disabled={busy} onClick={() => run('Reading controller readiness…', async () => {
        setControllers(null); const next = await rowynControllerScan(); setControllers(next); return 'Controller scan finished. Review findings below.';
      })}>Rescan controllers</button>
      {!controllers && <p className="card-copy">Not checked or unavailable. Scan errors appear above.</p>}
      {controllers?.map((item) => <div key={item.name}><h3>{item.name} <span className="rowyn-state">{item.state}</span></h3><pre>{item.detail}</pre></div>)}
    </section>
  </div>;
}
