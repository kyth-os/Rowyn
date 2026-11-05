export const CHECKLIST_VERSION = 1;
export const CHECKLIST_ITEMS = [
  { id: 'apps', label: 'Explore Apps', route: '/apps' },
  { id: 'updates', label: 'Check Updates', route: '/updates' },
  { id: 'development', label: 'Set up Development', route: '/development' },
  { id: 'diagnostics', label: 'Run Diagnostics', route: '/diagnostics' },
  { id: 'backups', label: 'Set up Backups', route: '/backups' },
] as const;
const KEY = 'rowyn.setup-checklist.v1';
export type ChecklistState = { version:  1; completed: string[]; skipped: string[]; dismissed: boolean };
export const emptyChecklist = (): ChecklistState => ({ version: 1, completed: [], skipped: [], dismissed: false });
export function parseChecklist(raw: string | null): ChecklistState {
  if (!raw) return emptyChecklist();
  try {
    const v = JSON.parse(raw);
    if (v?.version !== 1 || !Array.isArray(v.completed) || !Array.isArray(v.skipped) || typeof v.dismissed !== 'boolean') return emptyChecklist();
    const valid: Set<string> = new Set(CHECKLIST_ITEMS.map(x => x.id));
    const ids = (xs: unknown[]): string[] => [...new Set(xs.filter((x): x is string => typeof x === 'string' && valid.has(x)))];
    const completed = ids(v.completed);
    const skipped = ids(v.skipped).filter(id => !completed.includes(id));
    return { version: 1, completed, skipped, dismissed: v.dismissed };
  } catch { return emptyChecklist(); }
}
export function loadChecklist(storage?: Pick<Storage, 'getItem'> | null): ChecklistState {
  try {
    const source = storage === undefined ? (typeof localStorage === 'undefined' ? null : localStorage) : storage;
    return parseChecklist(source?.getItem(KEY) ?? null);
  } catch { return emptyChecklist(); }
}
export function saveChecklist(state: ChecklistState, storage?: Pick<Storage, 'setItem'> | null): boolean {
  try {
    const destination = storage === undefined ? (typeof localStorage === 'undefined' ? null : localStorage) : storage;
    destination?.setItem(KEY, JSON.stringify(state));
    return !!destination;
  } catch { return false; }
}
export function setChecklistItem(state: ChecklistState, id: string, status: 'completed' | 'skipped'): ChecklistState {
  if (!CHECKLIST_ITEMS.some(x => x.id === id)) return state;
  const other = status === 'completed' ? 'skipped' : 'completed';
  return { ...state, [status]: [...new Set([...state[status], id])], [other]: state[other].filter(x => x !== id) };
}
