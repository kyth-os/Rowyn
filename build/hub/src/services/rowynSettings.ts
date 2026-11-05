import { inTauriShell } from './tauriEnv';
import type { BackupSnapshot } from './rowynBackupModel';

export type SettingTarget = 'defaults' | 'file-associations' | 'flatpak-permissions' | 'bluetooth' | 'wifi' | 'audio' | 'displays' | 'backups';

export const SETTINGS_MODULES: Record<SettingTarget, string> = {
  defaults: 'kcm_componentchooser',
  'file-associations': 'kcm_filetypes',
  'flatpak-permissions': 'kcm_app-permissions',
  backups: 'kcm_kup',
  bluetooth: 'kcm_bluetooth',
  wifi: 'kcm_networkmanagement',
  audio: 'kcm_pulseaudio',
  displays: 'kcm_kscreen',
};

function requireNative(): void {
  if (!inTauriShell()) throw new Error('Available in the installed Rowyn Hub only.');
}

function bounded<T>(request: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error('The native settings request timed out. Refresh and try again.')), 12_000);
  });
  return Promise.race([request, timeout]).finally(() => clearTimeout(timer));
}

export async function openRowynSettings(target: SettingTarget): Promise<string> {
  requireNative();
  if (!Object.prototype.hasOwnProperty.call(SETTINGS_MODULES, target)) throw new Error('Unsupported settings target.');
  const { invoke } = await import('@tauri-apps/api/core');
  return bounded(invoke<string>('rowyn_open_settings', { target }));
}

export async function fetchRowynBackupsSnapshot(): Promise<BackupSnapshot> {
  requireNative();
  const { invoke } = await import('@tauri-apps/api/core');
  return bounded(invoke<BackupSnapshot>('rowyn_backups_snapshot'));
}
