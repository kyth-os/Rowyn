import { invoke } from '@tauri-apps/api/core';
import { inTauriShell } from './tauriEnv';
import type { DiagnosticsSnapshot, Reading } from './rowynMvpModel';

// Native reads have their own 8s-per-probe bounds and backend single-flight.
// No timer race here: clearing a UI timeout while native work continues would
// allow another click to stack work. The read settles only with the backend.
function nativeOnly() {
  if (!inTauriShell()) throw new Error('Available from the installed Rowyn Hub only.');
}
export async function rowynDiagnosticsSnapshot(): Promise<DiagnosticsSnapshot> {
  nativeOnly();
  return invoke<DiagnosticsSnapshot>('rowyn_diagnostics_snapshot');
}
export async function rowynControllerScan(): Promise<Reading[]> {
  nativeOnly();
  return invoke<Reading[]>('rowyn_controller_scan');
}
export async function exportRowynReport(report: string): Promise<string> {
  nativeOnly();
  return invoke<string>('rowyn_export_report', { report });
}
export async function openRowynLauncher(id: string): Promise<string> {
  nativeOnly();
  return invoke<string>('rowyn_launcher_open', { id });
}
