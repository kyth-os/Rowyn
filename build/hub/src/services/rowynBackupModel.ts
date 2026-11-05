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
