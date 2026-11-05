import { invoke } from '@tauri-apps/api/core';
import { inTauriShell } from './tauriEnv';
import { parseBootHealth, type BootHealthSnapshot } from './rowynBootHealthModel';

function nativeOnly() {
  if (!inTauriShell()) throw new Error('Available from the installed Rowyn Hub only.');
}

/**
 * Reads the image's boot-health record through the native bridge. The command
 * returns the raw file text (bounded, no symlinks, root-owned); validation and
 * projection stay in `rowynBootHealthModel` where they are unit-tested.
 */
export async function rowynBootHealthSnapshot(): Promise<BootHealthSnapshot> {
  nativeOnly();
  return parseBootHealth(await invoke<string>('rowyn_boot_health_snapshot'));
}
