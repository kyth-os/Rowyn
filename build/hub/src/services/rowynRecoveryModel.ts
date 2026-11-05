export const FULL_SHA256 = /^sha256:[a-f0-9]{64}$/;
export interface DeploymentIdentity { image: string | null; version: string | null; digest: string | null; timestamp: string | null; }
export interface RecoverySnapshot { booted: DeploymentIdentity | null; staged: DeploymentIdentity | null; rollback: DeploymentIdentity | null; }
const record = (v: unknown): Record<string, unknown> | null => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null;
const text = (v: unknown): string | null => typeof v === "string" && v.trim().length > 0 && v.length <= 2048 ? v : null;
function deployment(value: unknown): DeploymentIdentity | null {
 const entry = record(value); if (!entry) return {image:null,version:null,digest:null,timestamp:null};
 const i = record(entry.image), n = record(i?.image), n2 = record(n?.image);
 const image = text(typeof entry.image === "string" ? entry.image : i?.reference ?? (typeof i?.image === "string" ? i.image : n?.reference ?? (typeof n?.image === "string" ? n.image : n2?.reference ?? n2?.image)));
 const version = text(entry.version ?? i?.version ?? n?.version ?? n2?.version);
 const candidate = text(entry.imageDigest ?? entry.digest ?? i?.imageDigest ?? i?.digest ?? n?.imageDigest ?? n?.digest ?? n2?.imageDigest ?? n2?.digest);
 return { image, version, digest: candidate && FULL_SHA256.test(candidate) ? candidate : null, timestamp: text(entry.timestamp ?? i?.timestamp ?? n?.timestamp ?? n2?.timestamp ?? entry.created ?? entry.date) };
}
export function parseRecoverySnapshot(raw: unknown): RecoverySnapshot | null {
 const root = record(raw), status = record(root?.status); if (!status) return null;
 const parseSlot = (key: string) => status[key] == null ? null : deployment(status[key]);
 return { booted: parseSlot("booted"), staged: parseSlot("staged"), rollback: parseSlot("rollback") };
}
export function digestMatchedNotes(requested: unknown, history: unknown): { matched: boolean; highlights: string[]; title: string | null; version: string | null; published_at: string | null } {
 const fail = { matched: false, highlights: [] as string[], title: null as string | null, version: null as string | null, published_at: null as string | null };
 if (typeof requested !== "string" || !FULL_SHA256.test(requested) || !Array.isArray(history)) return fail;
 const entry = history.map(record).find(item => item?.digest === requested); if (!entry || !Array.isArray(entry.highlights)) return fail;
 return { matched: true, highlights: entry.highlights.filter((x): x is string => typeof x === "string" && x.length <= 2000).slice(0, 100), title: text(entry.highlights_title), version: text(entry.version), published_at: text(entry.published_at) };
}
