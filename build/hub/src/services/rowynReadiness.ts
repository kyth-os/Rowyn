import { inTauriShell } from './tauriEnv';
import { validateReadiness, type ReadinessSnapshot } from './rowynReadinessModel';

/** Explicit read only. Guard before loading native IPC; bound imports and invocation. */
export async function rowynReadinessSnapshot(): Promise<ReadinessSnapshot> {
  if (!inTauriShell()) throw new Error('Readiness checks are available in the installed Rowyn Hub only.');
  let timer: ReturnType<typeof setTimeout> | undefined;
  const request = (async () => {
    const { invoke } = await import('@tauri-apps/api/core');
    return invoke<unknown>('rowyn_readiness_snapshot');
  })();
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error('Readiness check timed out. Try again when the system is responding.')), 25_000);
  });
  try { return validateReadiness(await Promise.race([request, deadline])); }
  finally { if (timer !== undefined) clearTimeout(timer); }
}
