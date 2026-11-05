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
