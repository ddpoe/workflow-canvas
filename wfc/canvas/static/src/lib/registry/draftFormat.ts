/**
 * Text for the Registry's draft rows and Draft run tab: a folder path
 * clipped in the middle, a draft run's age, size and status tally, and the
 * draft string History is told to show for a draft run or one of its nodes.
 */
import type { DraftRunDetail, DraftRunNode } from './api.js';

/** The tabs of an open draft: its contract, or its latest draft run. */
export type DraftTab = 'parameters' | 'run';

/** The run-row status words a draft step uses. */
export type DraftStepStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled';

/** How each draft-step status reads in the Draft run tab, with its mark. */
export const STEP_WORD: Record<DraftStepStatus, { mark: string; word: string; cls: string }> = {
  success: { mark: '✓', word: 'completed', cls: 'ok' },
  failed: { mark: '✗', word: 'failed', cls: 'bad' },
  cancelled: { mark: '◼', word: 'cancelled', cls: 'can' },
  running: { mark: '●', word: 'running', cls: 'act' },
  pending: { mark: '○', word: 'pending', cls: 'dim' },
};

const TALLY_ORDER: DraftStepStatus[] = ['success', 'failed', 'cancelled', 'running', 'pending'];

/**
 * Clip a folder path in the middle, keeping its first part and its last
 * parts: `C:\Users\me\projects\lab\src\__demo__\label` reads
 * `C:\…\src\__demo__\label`. A path that fits is returned as is.
 *
 * Args:
 *     path: The path as the draft's pointer names it.
 *     max: The longest text to show.
 *
 * Returns:
 *     The path, or its first part, `…` and as many last parts as fit.
 */
export function clipMiddle(path: string, max = 40): string {
  if (path.length <= max) return path;
  const sep = path.includes('\\') ? '\\' : '/';
  const parts = path.split(sep);
  const head = parts[0] === '' ? `${sep}${parts[1] ?? ''}` : parts[0];
  const rest = parts[0] === '' ? parts.slice(2) : parts.slice(1);
  let tail: string[] = [];
  for (let i = rest.length - 1; i >= 0; i--) {
    const next = [rest[i], ...tail];
    if (tail.length > 0 && `${head}${sep}…${sep}${next.join(sep)}`.length > max) break;
    tail = next;
  }
  if (tail.length >= rest.length) return path;
  return `${head}${sep}…${sep}${tail.join(sep)}`;
}

/**
 * A byte count in the units the Registry uses (`2.4 MB`).
 *
 * Args:
 *     n: Bytes, or null when not known.
 *
 * Returns:
 *     The size, or `—` when not known.
 */
export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/**
 * How long ago an ISO 8601 time was, in words (`2 min ago`).
 *
 * Args:
 *     iso: The time.
 *     now: The current time in epoch milliseconds.
 *
 * Returns:
 *     `just now`, `N min ago`, `N h ago` or `N d ago`; the text as given
 *     when it is not a time.
 */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

/**
 * A draft run's steps by status, as the Status line lists them.
 *
 * Args:
 *     tally: Steps by status word.
 *
 * Returns:
 *     One entry per status with steps, completed first.
 */
export function tallyParts(
  tally: Record<string, number>,
): Array<{ n: number; mark: string; word: string; cls: string }> {
  return TALLY_ORDER
    .filter(s => (tally[s] ?? 0) > 0)
    .map(s => ({ n: tally[s], ...STEP_WORD[s] }));
}

/**
 * A check line's text, its `backticked` parts as code.
 *
 * Args:
 *     text: The line as the server wrote it.
 *
 * Returns:
 *     The parts in order, each marked code or plain.
 */
export function codeParts(text: string): Array<{ code: boolean; text: string }> {
  return text.split('`').map((t, i) => ({ code: i % 2 === 1, text: t }));
}

/**
 * The statuses of a draft step that has run and kept a result. A node lists
 * one step per sample, the ones that have not run as pending (or cancelled
 * once the run ended), and History has no such step to show.
 */
const RAN: ReadonlySet<string> = new Set(['success', 'failed']);

/**
 * The draft step a node line names in History: its first step that has run.
 *
 * Args:
 *     node: A node line of the Draft run tab.
 *
 * Returns:
 *     That step's draft string, or null while none of the node's steps has run.
 */
export function nodeDraftRef(node: DraftRunNode): string | null {
  return (node.steps ?? []).find(s => RAN.has(s.status))?.draftString ?? null;
}

/**
 * The draft step History highlights for a whole draft run: the draft's own
 * first step that has run, else the first step any node ran.
 *
 * Args:
 *     run: The draft's latest draft run.
 *
 * Returns:
 *     That step's draft string, or null when no node has a draft step.
 */
export function draftRunRef(run: DraftRunDetail): string | null {
  const nodes = run.nodes ?? [];
  for (const node of [...nodes.filter(n => n.isDraft), ...nodes]) {
    const ref = nodeDraftRef(node);
    if (ref) return ref;
  }
  return null;
}
