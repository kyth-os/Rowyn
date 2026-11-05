/**
 * Copy text without assuming the webview grants the async clipboard API.
 *
 * `navigator.clipboard` needs a secure context and a permission the Tauri
 * webview does not always give, which turned "Copy report" into a silent
 * failure. Try it first, then fall back to the selection-based copy that works
 * in any renderer, and report a typed failure so the page can point at the
 * native "Save report" path instead of claiming success.
 */
export type CopyResult =
  | { ok: true; method: 'clipboard' | 'selection' }
  | { ok: false; reason: string };

type CopyClipboard = { writeText?(value: string): Promise<void> };
type CopyArea = {
  value: string;
  style: Record<string, string>;
  setAttribute(name: string, value: string): void;
  select(): void;
};
type CopyDocument = {
  createElement(tag: string): CopyArea;
  body: { appendChild(node: unknown): void; removeChild(node: unknown): void };
  execCommand?(command: string): boolean;
};
export interface CopyEnv {
  clipboard?: CopyClipboard | null;
  document?: CopyDocument | null;
}

const UNAVAILABLE =
  'The system clipboard rejected the copy. Use Save report to Downloads instead.';

export async function copyText(text: string, env: CopyEnv = {}): Promise<CopyResult> {
  if (!text) return { ok: false, reason: 'There is nothing to copy yet.' };
  const clipboard = env.clipboard ?? (typeof navigator !== 'undefined' ? navigator.clipboard : null);
  const document = env.document ?? (typeof globalThis.document !== 'undefined' ? globalThis.document as unknown as CopyDocument : null);
  try {
    if (clipboard?.writeText) {
      await clipboard.writeText(text);
      return { ok: true, method: 'clipboard' };
    }
  } catch {
    // Fall through to the selection copy.
  }
  try {
    if (!document || typeof document.execCommand !== 'function') {
      return { ok: false, reason: UNAVAILABLE };
    }
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand('copy') === true;
    document.body.removeChild(area);
    return copied ? { ok: true, method: 'selection' } : { ok: false, reason: UNAVAILABLE };
  } catch {
    return { ok: false, reason: UNAVAILABLE };
  }
}
