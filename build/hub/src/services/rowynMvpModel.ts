import type { InstalledFlatpaksResult } from './liveData';
export interface Reading { name: string; state: string; detail: string }
export interface SteamGame { app_id: string; name: string }
export interface SteamScan { available: boolean; warnings: number; games: SteamGame[] }
export interface DiagnosticsSnapshot { collected_at: number; readings: Reading[] }
export const LAUNCHERS = [
  { id: 'com.valvesoftware.Steam', name: 'Steam', description: 'Steam library and Proton. Enable Steam Play per title when needed.' },
  { id: 'com.heroicgameslauncher.hgl', name: 'Heroic', description: 'Epic, GOG and Amazon libraries; sign in inside the launcher.' },
  { id: 'net.lutris.Lutris', name: 'Lutris', description: 'Community-managed game installers; review scripts before using them.' },
  { id: 'com.usebottles.bottles', name: 'Bottles', description: 'Isolated Wine environments. Not every Windows game is supported.' },
] as const;
export function requireLauncher(id: string) {
  const launcher = LAUNCHERS.find((entry) => entry.id === id);
  if (!launcher) throw new Error('Unsupported launcher.');
  return launcher;
}
export function launcherState(id: string, scan: InstalledFlatpaksResult | null): 'installed' | 'not installed' | 'unavailable' {
  requireLauncher(id);
  if (scan?.apps.some((app) => app.id === id)) return 'installed';
  return !scan || scan.failedScopes.length ? 'unavailable' : 'not installed';
}
export async function installLauncher(id: string, ops: { install: (id: string) => Promise<string>; wait: (job: string, limit: number) => Promise<string> }, onStarted: (job: string) => void = () => {}): Promise<string> {
  requireLauncher(id);
  const job = await ops.install(id);
  const pending = ops.wait(job, 1800); // registers the shared cancellation slot synchronously
  onStarted(job);
  return pending;
}
export function createPlayStatusOwner(write: (message: string) => void) {
  let generation = 0;
  const message = (error: unknown) => error instanceof Error ? error.message : String(error);
  return {
    async run(pending: string, action: () => Promise<string>) {
      const ticket = ++generation; write(pending);
      try { const result = await action(); if (ticket === generation) write(result); }
      catch (error) { if (ticket === generation) write(`Failed: ${message(error)}`); }
    },
    async cancel(action: () => Promise<string>) {
      const ticket = ++generation; write('Cancelling install…');
      try { const result = await action(); if (ticket === generation) write(result); }
      catch (error) { if (ticket === generation) write(`Cancel failed: ${message(error)}`); }
    },
  };
}
export function launchOptions(boundary: 'flatpak' | 'host', gaming: boolean, mimalloc: boolean): string {
  if (boundary === 'flatpak') return '%command%';
  return [gaming ? '/usr/bin/rowyn-game-launch' : '', mimalloc ? '/usr/bin/rowyn-mimalloc-run' : '', '%command%'].filter(Boolean).join(' ');
}
export function steamCompatibilityLinks(appId: string) {
  if (!/^[1-9][0-9]{0,9}$/.test(appId)) throw new Error('invalid Steam app id');
  return {
    protondb: `https://www.protondb.com/app/${appId}`,
    steamStore: `https://store.steampowered.com/app/${appId}`,
  };
}
export function formatReport(snapshot: DiagnosticsSnapshot): string {
  return ['Rowyn Diagnostics — read-only', `Collected: ${new Date(snapshot.collected_at * 1000).toISOString()}`,
    'Scrubbed locally; redaction is best effort. Review before sharing. Nothing uploaded.', '',
    ...snapshot.readings.slice(0, 16).map((item) => `${item.name} [${item.state}]: ${item.detail}`)].join('\n\n');
}
