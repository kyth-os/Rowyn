import { inTauriShell } from './tauriEnv';
import type { PrintQueuesResult } from './rowynWorkModel';

/** Reads CUPS queues. Throws with the bridge's own explanation so the page
 * can show why reading failed instead of reporting "no printers". */
export async function readPrintQueues(): Promise<PrintQueuesResult> {
  if (!inTauriShell()) throw new Error('Printing is available from the installed Rowyn Hub.');
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<PrintQueuesResult>('rowyn_print_queues');
}

/** Mount-time read: an unreadable bridge leaves the card unknown. */
export async function fetchPrintQueues(): Promise<PrintQueuesResult | null> {
  try { return await readPrintQueues(); } catch { return null; }
}

/** One test page on the named queue. The caller owns the confirmation. */
export async function printTestPage(queue: string): Promise<string> {
  if (!inTauriShell()) throw new Error('Printing is available from the installed Rowyn Hub.');
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<string>('rowyn_print_test_page', { queue });
}

/** Creates one new driverless queue. The caller owns the confirmation; the
 * bridge rejects a name that already exists rather than rewriting it. */
export async function addPrintQueue(queue: string, uri: string): Promise<string> {
  if (!inTauriShell()) throw new Error('Adding a printer is available from the installed Rowyn Hub.');
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<string>('rowyn_print_add_queue', { queue, uri });
}
