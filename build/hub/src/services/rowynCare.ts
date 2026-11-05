import { parsePowerSnapshot, parseStorageSnapshot } from './rowynCareModel';
async function nativeSnapshot<T>(command: string, parse: (value: unknown) => T): Promise<T> {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) throw new Error('Available in the installed Rowyn Hub only.');
  const { invoke } = await import('@tauri-apps/api/core');
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const timeout = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('Native care request timed out.')), 12_000); });
    return parse(await Promise.race([invoke<unknown>(command), timeout]));
  } finally { if (timer !== undefined) clearTimeout(timer); }
}
export const rowynStorageSnapshot = () => nativeSnapshot('rowyn_storage_snapshot', parseStorageSnapshot);
export const rowynPowerSnapshot = () => nativeSnapshot('rowyn_power_snapshot', parsePowerSnapshot);
