export type BackupState = 'plans' | 'none' | 'unavailable' | 'error';
export type BackupKind = 'versioned' | 'mirror';
export interface BackupPlan {
  name: string;
  destination: string;
  included: string[];
  excluded: string[];
  lastCompleted: string | null;
  kind: BackupKind;
  enabled: boolean;
  defaultIncluded: boolean;
}
export interface BackupSnapshot {
  state: BackupState;
  detail: string;
  plans: BackupPlan[];
}
export function backupSummary(snapshot: BackupSnapshot): string {
  if (snapshot.state === 'plans') return `${snapshot.plans.length} Kup backup plan(s) configured.`;
  if (snapshot.state === 'none') return snapshot.detail;
  return snapshot.detail;
}
export function backupStatus(plan: BackupPlan): string {
  return plan.enabled ? 'Enabled' : 'Disabled';
}

export const STALE_AFTER_DAYS = 7;
export const OLD_AFTER_DAYS = 30;
const DAY_MS = 86_400_000;

export type FreshnessState = 'unknown' | 'fresh' | 'stale' | 'old';
export interface BackupFreshness {
  state: FreshnessState;
  days: number | null;
  label: string;
}

/**
 * Kup records the last completed run; alone it reads as reassurance even when
 * it is months old. Derive an explicit age so the page can say "stale" instead
 * of printing a date the reader has to interpret. Kup's record is not an
 * independent verification — the label is about age, not success.
 */
export function backupFreshness(
  lastCompleted: string | null | undefined,
  now: number = Date.now(),
  staleAfterDays = STALE_AFTER_DAYS,
): BackupFreshness {
  if (!lastCompleted) return { state: 'unknown', days: null, label: 'No completed backup recorded by Kup' };
  const recorded = Date.parse(lastCompleted);
  if (!Number.isFinite(recorded)) {
    return { state: 'unknown', days: null, label: 'Recorded date is unreadable; review the plan in Kup settings' };
  }
  const days = Math.max(0, Math.floor((now - recorded) / DAY_MS));
  if (days >= OLD_AFTER_DAYS) return { state: 'old', days, label: `${days} days old; likely out of date` };
  if (days >= staleAfterDays) return { state: 'stale', days, label: `${days} days old; due for another run` };
  return { state: 'fresh', days, label: days === 0 ? 'Completed today' : `${days} day(s) ago` };
}
