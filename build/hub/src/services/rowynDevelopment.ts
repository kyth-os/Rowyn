import { invoke } from '@tauri-apps/api/core';
import { inTauriShell } from './tauriEnv';

export type DevelopmentState = 'absent' | 'managed' | 'collision' | 'unavailable';
export type DevelopmentJob = {
  id: string;
  status: 'running' | 'succeeded' | 'failed' | 'cancelled';
  detail: string;
};

// Keep only the active operation id across route mounts in this JS process.
// Never persist it: a backend restart invalidates native job ids.
let activeJobId: string | null = null;

export function getDevelopmentActiveJobId(): string | null {
  return activeJobId;
}

export function setDevelopmentActiveJobId(id: string | null): void {
  activeJobId = id;
}

function nativeOnly() {
  if (!inTauriShell()) throw new Error('Available from the installed Rowyn Hub only.');
}

export async function rowynDevelopmentList(): Promise<{
  state: DevelopmentState;
  name: string;
  image: string;
  status: string;
  detail: string;
}> {
  nativeOnly();
  return invoke('rowyn_development_list');
}

function reconcileJob(job: DevelopmentJob): DevelopmentJob {
  if (job.status === 'running') {
    activeJobId = job.id;
  } else if (activeJobId === job.id) {
    activeJobId = null;
  }
  return job;
}

export type DevelopmentPlan = { preset: string; target: string; packages: string[]; commands: string[][] };
export async function rowynDevelopmentPresetPreview(preset: string): Promise<DevelopmentPlan> {
  nativeOnly();
  return invoke('rowyn_development_preset_preview', { preset });
}

export async function rowynDevelopmentStart(
  operation: 'create' | 'delete' | `preset:${string}`,
  confirmation?: string,
): Promise<DevelopmentJob> {
  nativeOnly();
  const job = await invoke<DevelopmentJob>('rowyn_development_start', { operation, confirmation });
  return reconcileJob(job);
}

export async function rowynDevelopmentJob(id: string): Promise<DevelopmentJob> {
  nativeOnly();
  const job = await invoke<DevelopmentJob>('rowyn_development_job', { id });
  return reconcileJob(job);
}

export async function rowynDevelopmentCancel(id: string): Promise<DevelopmentJob> {
  nativeOnly();
  const job = await invoke<DevelopmentJob>('rowyn_development_cancel', { id });
  return reconcileJob(job);
}

export async function rowynDevelopmentOpen(): Promise<string> {
  nativeOnly();
  return invoke('rowyn_development_open');
}
