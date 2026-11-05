/** Pure helpers for the Work page's printing card.
 *
 * The bridge reads CUPS; nothing here invents a queue, a default, or a
 * "ready" state CUPS did not report. Unknown is its own answer.
 */

export interface PrintQueue {
  name: string;
  state: string;
  is_default: boolean;
}

export interface PrintQueuesResult {
  queues: PrintQueue[];
  default_queue: string | null;
  detail: string;
}

export function queueStateLabel(state: string): string {
  switch (state) {
    case 'idle':
      return 'idle — ready to print';
    case 'printing':
      return 'printing now';
    case 'disabled':
      return 'disabled — jobs are held';
    default:
      return `unknown state (${state || 'not reported'})`;
  }
}

/** One sentence about the queues that were actually read. */
export function queuesSummary(result: PrintQueuesResult | null): string {
  if (!result) return 'Print queues have not been read, so printing state is unknown rather than empty.';
  if (!result.queues.length) {
    return 'No print queues are configured. Add a printer in Print settings, then read the queues again.';
  }
  const names = result.queues.map((queue) => queue.name).join(', ');
  const hasDefault = result.default_queue !== null
    && result.queues.some((queue) => queue.name === result.default_queue);
  const fallback = hasDefault
    ? `Default queue: ${result.default_queue}.`
    : 'No default queue is set, so each job has to name its queue.';
  return `${result.queues.length} queue${result.queues.length === 1 ? '' : 's'} configured (${names}). ${fallback}`;
}

export function testPagePrompt(queue: string): string {
  return `Print one test page on ${queue}? CUPS sends it to the printer now. The queue, its options and every other printer stay as they are, and only the printer can confirm the page came out.`;
}

/** A discovered IPP service that can become a print queue. */
export interface DiscoveredPrinter {
  name: string;
  uri: string;
}

export interface DiscoveredPrinters {
  addable: DiscoveredPrinter[];
  /** Entries `lpstat -e` reported: already queues, so never re-added. */
  existing: string[];
  /** Anything that is neither an IPP address nor a queue name. */
  unsupported: string[];
}

const IPP_SCHEMES = ['ipp://', 'ipps://'];

/** `ipp://Office%20Printer._ipp._tcp.local/` -> `Office_Printer`.
 * A queue name is not cosmetic: it is passed to `lpadmin -p`, so only
 * characters CUPS accepts are kept. */
function queueNameFromUri(uri: string): string | null {
  const afterScheme = uri.slice(uri.indexOf('://') + 3);
  const authority = afterScheme.split('/')[0].split(':')[0];
  if (!authority) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(authority);
  } catch {
    return null;
  }
  const base = decoded
    .replace(/\._ipps?\._tcp\.local\.?$/i, '')
    .replace(/\.local\.?$/i, '');
  const cleaned = base.replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40);
  return cleaned || null;
}

/** Splits a discovery listing into printers this page can add and entries it
 * must not touch. Discovery mixes `ippfind` addresses with `lpstat -e` queue
 * names, and re-adding a queue name would rewrite that queue. */
export function parseDiscoveredPrinters(entries: string[]): DiscoveredPrinters {
  const addable: DiscoveredPrinter[] = [];
  const existing: string[] = [];
  const unsupported: string[] = [];
  const taken = new Set<string>();
  for (const raw of entries) {
    const entry = raw.trim();
    if (!entry) continue;
    const lowered = entry.toLowerCase();
    if (!IPP_SCHEMES.some((scheme) => lowered.startsWith(scheme))) {
      if (/^[A-Za-z0-9_.-]{1,64}$/.test(entry)) {
        if (!existing.includes(entry)) existing.push(entry);
      } else {
        unsupported.push(entry);
      }
      continue;
    }
    const name = queueNameFromUri(entry);
    if (!name) {
      unsupported.push(entry);
      continue;
    }
    if (taken.has(name)) continue;
    taken.add(name);
    addable.push({ name, uri: entry });
  }
  return { addable, existing, unsupported };
}

export function addQueuePrompt(name: string, uri: string): string {
  return `Create a print queue named ${name} for ${uri}? Rowyn asks CUPS to add it with the driverless “everywhere” model, so no vendor driver is installed. Adding a printer is an administrator action: CUPS can refuse if this account may not administer printers, and existing queues are not changed.`;
}
