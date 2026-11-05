import { invoke } from "@tauri-apps/api/core";
import { fetchUpdateReleaseHistory } from "./liveData";
import { parseRecoverySnapshot, type RecoverySnapshot } from "./rowynRecoveryModel";
export async function fetchRecoverySnapshot(): Promise<RecoverySnapshot | null> {
 if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return null;
 try { const response = await invoke<{ data?: unknown }>("probe_backend", { section: "bootc-status-data" }); return parseRecoverySnapshot(response?.data); } catch { return null; }
}
export async function fetchRecoveryHistory(): Promise<unknown[] | null> {
 try { const history = await fetchUpdateReleaseHistory(); return Array.isArray(history) ? history : null; } catch { return null; }
}
