import { useEffect, useRef, useState } from 'react';
import {
  cancelInstall,
  convertPst,
  createM365Shortcuts,
  fetchFocusStatus,
  fetchFontsReady,
  fetchInstalledFlatpaks,
  fetchNetworkSummary,
  fetchPrinterDiscover,
  fetchPstFiles,
  installFlatpak,
  openM365App,
  startFocusSession,
  stopFocusSession,
  waitInstallJob,
  type FocusStatus,
  type NetworkSummary,
} from '../services/liveData';
import { openRowynSettings } from '../services/rowynSettings';
import { printTestPage, readPrintQueues } from '../services/rowynWork';
import { queueStateLabel, queuesSummary, testPagePrompt, type PrintQueuesResult } from '../services/rowynWorkModel';
import { useSectionFollow, type SectionMap } from '../hooks/useSectionFollow';
import '../styles/rowynMvp.css';

const SECTIONS: SectionMap = {
  Printing: 'hub-section-printing',
  Office: 'hub-section-office',
  Focus: 'hub-section-focus',
};

const OFFICE_APPS: [string, string][] = [
  ['org.libreoffice.LibreOffice', 'LibreOffice'],
  ['eu.betterbird.Betterbird', 'Betterbird'],
];
const M365_APPS = ['Outlook', 'Word', 'Excel', 'PowerPoint', 'OneNote', 'Teams'];

function stateTone(good: boolean | null): string {
  return good === null ? 'work-card-muted' : good ? 'work-card-ok' : 'work-card-warn';
}

function WorkCard({ icon, label, value, detail, good }: { icon: string; label: string; value: string; detail: string; good: boolean | null }) {
  return <article className={`work-card ${stateTone(good)}`}><div className="work-card-top"><span className="work-card-icon" aria-hidden="true">{icon}</span><span className="work-card-label">{label}</span><span className="work-status-dot" /></div><strong className="work-card-value">{value}</strong><span className="work-card-detail">{detail}</span></article>;
}

// Work is its own destination: printing, office documents and focus time are
// workday tools, not app discovery. Every read here is local and cheap, so the
// page loads its real state once on open instead of sitting "unavailable"
// until someone presses a button; nothing polls, and the printer scan — the
// only network probe — stays manual.
export function Work() {
  useSectionFollow(SECTIONS);
  const [status, setStatus] = useState('Reading printing, fonts and focus state…');
  const [busy, setBusy] = useState<string | null>(null);
  const [queues, setQueues] = useState<PrintQueuesResult | null>(null);
  const [printers, setPrinters] = useState<string[] | null>(null);
  const [pendingTest, setPendingTest] = useState<string | null>(null);
  const [fonts, setFonts] = useState<{ ready: boolean; detail: string } | null>(null);
  const [summary, setSummary] = useState<NetworkSummary | null>(null);
  const [installed, setInstalled] = useState<string[]>([]);
  const [pstFiles, setPstFiles] = useState<string[] | null>(null);
  const [focus, setFocus] = useState<FocusStatus | null>(null);
  const [focusMinutes, setFocusMinutes] = useState(25);
  const lock = useRef(false);

  async function run(key: string, pending: string, action: () => Promise<string>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(key);
    setStatus(pending);
    try {
      setStatus(await action());
    } catch (error) {
      setStatus(`Failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      lock.current = false;
      setBusy(null);
    }
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [queueRead, fontRead, networkRead, appsRead, focusRead] = await Promise.all([
        readPrintQueues().catch(() => null),
        fetchFontsReady(),
        fetchNetworkSummary(),
        fetchInstalledFlatpaks(),
        fetchFocusStatus(),
      ]);
      if (cancelled) return;
      setQueues(queueRead);
      setFonts(fontRead);
      setSummary(networkRead);
      setInstalled((appsRead?.apps ?? []).map((app) => app.id));
      setFocus(focusRead);
      setStatus(queueRead
        ? queuesSummary(queueRead)
        : 'Print queues could not be read, so printing state is unknown rather than empty. Check that cups.service is running, then read the queues again.');
    })();
    return () => { cancelled = true; };
  }, []);

  async function confirmTestPage() {
    const queue = pendingTest;
    if (!queue || lock.current) return;
    setPendingTest(null);
    await run('test-page', `Sending a test page to ${queue}…`, () => printTestPage(queue));
  }

  const defaultQueue = queues?.default_queue ?? null;
  const focusMinutesLeft = focus ? Math.ceil(focus.remaining_secs / 60) : 0;

  return <div className="page-content rowyn-mvp">
    <section className="glass dashboard-card" id="hub-section-printing">
      <span className="home-eyebrow">Rowyn Work · MVP</span><h1>Printing</h1>
      <p className="card-copy">Printing runs through CUPS. Network printers are found over mDNS and printed to driverless (IPP Everywhere) queues; USB printers are published by ipp-usb, which this image ships. No vendor driver is installed, and no queue is added, changed or removed by this page — Print settings owns that. Reading queues and sending one test page are the only actions here.</p>
      <div className="home-actions">
        <button className="action-button" disabled={busy !== null} onClick={() => void run('queues', 'Reading print queues…', async () => {
          const next = await readPrintQueues();
          setQueues(next);
          return queuesSummary(next);
        })}>Read print queues</button>
        <button className="action-button" disabled={busy !== null} onClick={() => void run('discover', 'Scanning the network for IPP printers…', async () => {
          const found = await fetchPrinterDiscover();
          if (!found) return 'Printer discovery is unavailable outside the Hub shell.';
          setPrinters(found);
          return found.length > 0
            ? `${found.length} printer(s) answered. A discovered printer still needs a queue in Print settings before it can be used.`
            : 'No network printers answered. USB printers are reached through ipp-usb, and a printer on another subnet may not answer mDNS.';
        })}>Scan for network printers</button>
        <button className="action-button" disabled={busy !== null} onClick={() => void run('settings', 'Opening Print settings…', () => openRowynSettings('printers'))}>Open printer settings</button>
      </div>
      <p className="card-copy" role="status">{status}</p>
      <div className="work-card-grid">
        <WorkCard icon="▣" label="Print queues" value={queues === null ? 'Not read' : queues.queues.length === 0 ? 'None configured' : `${queues.queues.length} configured`} detail={queuesSummary(queues)} good={queues === null ? null : queues.queues.length > 0} />
        <WorkCard icon="◎" label="Default queue" value={defaultQueue ?? (queues === null ? 'Unknown' : 'None set')} detail={defaultQueue ? 'Jobs without an explicit queue go here.' : 'Choose a default in Print settings, or name the queue for each job.'} good={defaultQueue ? true : queues === null ? null : false} />
        <WorkCard icon="⌁" label="Network printers found" value={printers === null ? 'Not scanned' : printers.length === 0 ? 'None answered' : `${printers.length} answered`} detail={printers === null ? 'Scanning is an explicit IPP/mDNS probe; nothing runs on its own.' : printers.length > 0 ? printers.join(' · ') : 'No network printer answered. That is not proof none exist on this network.'} good={printers === null ? null : printers.length > 0} />
      </div>
      {queues?.queues.length ? <div>{queues.queues.map((queue) => <article className="rowyn-launcher" key={queue.name}>
        <h3>{queue.name} <span className="rowyn-state">{queueStateLabel(queue.state)}</span>{queue.is_default && <span className="pill pill-ok">default</span>}</h3>
        <p className="card-copy">Reported by CUPS · {queue.is_default ? 'this is the default queue' : 'not the default queue'}.</p>
        <div className="home-actions"><button className="action-button" disabled={busy !== null || pendingTest !== null} onClick={() => setPendingTest(queue.name)}>Print test page on {queue.name}</button></div>
      </article>)}</div> : null}
      {pendingTest && <section className="glass dashboard-card" role="alertdialog" aria-labelledby="rowyn-work-test-page">
        <h2 id="rowyn-work-test-page">Print a test page?</h2>
        <p className="card-copy">{testPagePrompt(pendingTest)}</p>
        <div className="home-actions">
          <button className="action-button" disabled={busy !== null} onClick={() => setPendingTest(null)}>Cancel</button>
          <button className="action-button" disabled={busy !== null} onClick={() => void confirmTestPage()}>Print test page</button>
        </div>
      </section>}
      <p className="card-copy">A queued job is not a printed page: the printer reports the outcome, and the Hub does not claim a test print succeeded.</p>
    </section>

    <section className="glass dashboard-card" id="hub-section-office">
      <h2>Office and mail</h2>
      <p className="card-copy">Documents, mail and calendars. LibreOffice and Betterbird install from Flathub with the same jobs as the App Store; the Microsoft 365 entries are web apps in their own browser tab, never a local Office install.</p>
      <div className="work-card-grid">
        <WorkCard icon="Aa" label="Office fonts" value={fonts ? fonts.ready ? 'Installed' : 'Needs setup' : 'Checking…'} detail={fonts?.detail || 'Metric-compatible fonts (Liberation) keep Office documents laid out correctly.'} good={fonts ? fonts.ready : null} />
        <WorkCard icon="◫" label="Office suite" value={installed.includes(OFFICE_APPS[0][0]) ? 'LibreOffice installed' : 'Not installed'} detail="Writer, Calc and Impress from Flathub; the image ships no deb LibreOffice." good={installed.includes(OFFICE_APPS[0][0])} />
        <WorkCard icon="✉" label="Mail client" value={installed.includes(OFFICE_APPS[1][0]) ? 'Betterbird installed' : 'Not installed'} detail="Thunderbird-compatible mail and calendar from Flathub." good={installed.includes(OFFICE_APPS[1][0])} />
      </div>
      <div className="home-actions">
        {OFFICE_APPS.map(([id, name]) => installed.includes(id)
          ? <span key={id} className="pill pill-ok">{name} installed</span>
          : <button key={id} className="action-button" disabled={busy !== null} onClick={() => void run(`install-${id}`, `Installing ${name}…`, async () => {
            const detail = await waitInstallJob(await installFlatpak(id));
            if (detail === 'Installed successfully.') setInstalled((current) => [...new Set([...current, id])]);
            return detail;
          })}>Install {name}</button>)}
        {busy?.startsWith('install-') && <button className="action-button" disabled={busy === 'cancel'} onClick={() => void run('cancel', 'Cancelling the install…', canonicalCancel)}>Cancel install</button>}
      </div>
      <p className="card-copy">Microsoft 365 opens a fixed set of web apps in a dedicated browser tab; Add to application menu creates launchers for the same six. No credential is stored by the Hub.</p>
      <div className="home-actions">
        {M365_APPS.map((name) => <button key={name} className="action-button" disabled={busy !== null} onClick={() => void run(`m365-${name}`, `Opening ${name}…`, () => openM365App(name))}>{name}</button>)}
        <button className="action-button" disabled={busy !== null} onClick={() => void run('m365-shortcuts', 'Adding Microsoft 365 shortcuts…', createM365Shortcuts)}>Add to application menu</button>
      </div>
      <p className="card-copy">Outlook archives: find .pst/.ost files in your user folders and convert them into mail folders under Documents/Outlook Import. The originals are kept.</p>
      <div className="home-actions">
        <button className="action-button" disabled={busy !== null} onClick={() => void run('pst-scan', 'Scanning for Outlook archives…', async () => {
          const files = await fetchPstFiles();
          setPstFiles(files);
          return files ? `${files.length} archive(s) found.` : 'Archive scanning is unavailable outside the Hub shell.';
        })}>Find Outlook archives</button>
        {busy?.startsWith('pst-') && <button className="action-button" disabled={busy === 'cancel'} onClick={() => void run('cancel', 'Cancelling the conversion…', canonicalCancel)}>Cancel conversion</button>}
      </div>
      {pstFiles && (pstFiles.length ? <div>{pstFiles.map((path) => <div key={path} className="home-actions" style={{ justifyContent: 'space-between' }}>
        <span className="card-copy" style={{ flex: 1, fontSize: 12 }}>{path}</span>
        <button className="action-button" disabled={busy !== null} onClick={() => void run(`pst-${path}`, 'Converting the Outlook archive…', () => convertPst(path))}>Convert</button>
      </div>)}</div> : <p className="card-copy">No archives found in Documents, Downloads, local app data, or mounted user drives.</p>)}
    </section>

    <section className="glass dashboard-card" id="hub-section-focus">
      <h2>Focus and connectivity</h2>
      <p className="card-copy">A focus session keeps the PC awake for a timed block and restores normal power behavior when it ends. The remaining time below is a reading taken when this page loaded — the Hub does not tick a clock in the background.</p>
      <div className="work-card-grid">
        <WorkCard icon="◷" label="Focus session" value={focus ? `Active · about ${focusMinutesLeft} min left` : 'Not running'} detail={focus ? 'Ending it restores normal suspend and screen-blank behavior.' : 'No session is holding the PC awake for this page.'} good={focus ? true : null} />
        <WorkCard icon="☁" label="Connected services" value={summary ? `${summary.cloudProviders.length} cloud provider${summary.cloudProviders.length === 1 ? '' : 's'}` : 'Checking…'} detail={summary?.detail || 'VPN and cloud connectivity are being checked.'} good={summary ? summary.cloudProviders.length > 0 || summary.vpnConnected : null} />
      </div>
      <div className="home-actions">
        {!focus ? <>
          <select value={focusMinutes} onChange={(event) => setFocusMinutes(Number(event.target.value))} aria-label="Focus session length">
            <option value={25}>25 minutes</option><option value={50}>50 minutes</option><option value={90}>90 minutes</option>
          </select>
          <button className="action-button" disabled={busy !== null} onClick={() => void run('focus-start', 'Starting the focus session…', async () => {
            const id = await startFocusSession(focusMinutes);
            setFocus({ id, remaining_secs: focusMinutes * 60 });
            return `Focus session started for ${focusMinutes} minutes.`;
          })}>Start focus session</button>
        </> : <button className="action-button" disabled={busy !== null} onClick={() => void run('focus-stop', 'Ending the focus session…', async () => {
          const result = await stopFocusSession(focus.id);
          setFocus(null);
          return result;
        })}>End focus session</button>}
        <button className="action-button" disabled={busy !== null} onClick={() => void run('focus-read', 'Reading the focus session…', async () => {
          const next = await fetchFocusStatus();
          setFocus(next);
          return next ? `Focus session active with about ${Math.ceil(next.remaining_secs / 60)} min left.` : 'No focus session is running.';
        })}>Refresh focus state</button>
      </div>
      <p className="card-copy">Focus sessions use the same systemd-inhibit lock the shell reaps after a crash, so a session cannot outlive the Hub process.</p>
    </section>
  </div>;
}

/** Cancel the shared install/convert job; shared by both cards. */
async function canonicalCancel(): Promise<string> {
  return cancelInstall();
}
