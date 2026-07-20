/**
 * Archive-status polling store for the unarchived-cache toolbar badge.
 */
import { writable } from 'svelte/store';

export interface ArchiveOutputEntry {
  name: string;
  /** `archived | hashing | pending | missing | error:...` */
  status: string;
}

export interface ArchiveRunProgress {
  run_id: number;
  label: string;
  done: number;
  total: number;
  outputs: ArchiveOutputEntry[];
}

export interface ArchiveProgress {
  runs_done: number;
  runs_total: number;
  current_output: string | null;
  per_run: ArchiveRunProgress[];
}

export interface ArchiveStatus {
  state: 'idle' | 'archiving';
  unarchived_runs: number;
  unarchived_outputs: number;
  pipeline_running: boolean;
  progress: ArchiveProgress | null;
}

/** Latest archive-status payload; null until the first successful poll. */
export const archiveStatus = writable<ArchiveStatus | null>(null);

// Idle cadence matches the existing run-status poll; tighten while an
// archive pass is ticking so the popover's current-output line is live.
export const IDLE_POLL_MS = 2500;
export const ACTIVE_POLL_MS = 1000;

export async function refreshArchiveStatus(): Promise<void> {
  try {
    const resp = await fetch('/api/wfc/archive-status');
    if (!resp.ok) return;
    archiveStatus.set(await resp.json());
  } catch {
    // Server unreachable — keep the last known state. The badge only
    // mirrors DB state; the next successful poll corrects it.
  }
}

/**
 * POST cache/archive, then refresh immediately so the badge flips to
 * the archiving state without waiting for the next poll. A 409 (job
 * already running / pipeline in flight) needs no special handling —
 * the refreshed status already reflects whatever is actually running.
 */
export async function startArchive(): Promise<void> {
  try {
    await fetch('/api/wfc/cache/archive', { method: 'POST' });
  } catch {
    // Network failure — nothing started; the poll loop keeps ruling.
  }
  await refreshArchiveStatus();
}
