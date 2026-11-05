/**
 * Whether Home's two read-only status sources exist on this host.
 *
 * Home hides its whole status row when neither the bootc update status nor the
 * image's boot-health record can be read, so a machine that is not a Rowyn
 * image does not open on two permanent failure cards. The panels report what
 * they actually found; nothing here probes the system itself.
 */
export type StatusSource = 'updates' | 'bootHealth';

const present: Record<StatusSource, boolean> = { updates: false, bootHealth: false };
const listeners = new Set<() => void>();

export function reportStatusSource(source: StatusSource, isPresent: boolean): void {
  if (present[source] === isPresent) return;
  present[source] = isPresent;
  for (const notify of listeners) notify();
}

export function statusRowVisible(): boolean {
  return present.updates || present.bootHealth;
}

export function subscribeStatusSources(notify: () => void): () => void {
  listeners.add(notify);
  return () => { listeners.delete(notify); };
}
