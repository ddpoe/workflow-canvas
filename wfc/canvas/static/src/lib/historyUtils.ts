/**
 * Shared utility functions for the History tab components.
 *
 * Extracted from PathsView, RunDetailPanel, and DescendantTree to eliminate
 * duplication.
 */

/**
 * Format a Unix-epoch millisecond timestamp into a human-readable string.
 *
 * Returns short date + time, e.g. "Apr 9, 2026 02:30 PM".
 */
export function formatTimestamp(ms: number): string {
  if (!ms) return '--';
  const d = new Date(ms);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/**
 * Format a duration in seconds into a human-readable string.
 *
 * Returns e.g. "12.3s" or "2m 15s".
 */
export function formatDuration(seconds: number): string {
  if (!seconds) return '--';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

/**
 * Format a byte count as a short human-readable string.
 * Returns e.g. "812 B", "1.4 KB", "23.7 MB".
 */
export function formatBytes(bytes: number): string {
  if (bytes == null || Number.isNaN(bytes)) return '--';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/**
 * Format a Unix-epoch millisecond timestamp as a compact relative time.
 * "just now", "3m ago", "2h ago", "4d ago", else the short date.
 */
export function formatRelativeTime(ms: number): string {
  if (!ms) return '--';
  const delta = Date.now() - ms;
  if (delta < 0) return 'just now';
  const sec = Math.floor(delta / 1000);
  if (sec < 60) return 'just now';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/**
 * Map a run status string to a CSS color variable.
 */
export function statusColor(status: string): string {
  switch (status) {
    case 'success': return 'var(--color-completed, #50C878)';
    case 'failed': return 'var(--color-failed, #E74C3C)';
    case 'running': return 'var(--color-running, #E9A847)';
    case 'cancelled': return 'var(--color-cancelled, #7f8ea3)';
    default: return 'var(--text-muted, #666)';
  }
}

/**
 * Deterministic module name to HSL color.
 *
 * Ported from the original workflow_canvas nodes.js getModuleColor().
 * Uses a simple string hash to pick a hue, with fixed saturation and lightness
 * for good contrast on dark backgrounds.
 */
export function getModuleColor(moduleName: string): string {
  let hash = 0;
  for (let i = 0; i < moduleName.length; i++) {
    hash = moduleName.charCodeAt(i) + ((hash << 5) - hash);
    hash = hash & hash; // Convert to 32-bit integer
  }
  const hue = ((hash % 360) + 360) % 360;
  return `hsl(${hue}, 55%, 55%)`;
}

/**
 * Convert an HSL color string to an RGBA string with the given alpha.
 *
 * Parses "hsl(H, S%, L%)" and returns "rgba(R, G, B, A)".
 */
export function hslToRgba(hsl: string, alpha: number): string {
  const match = hsl.match(/hsl\((\d+),\s*(\d+)%,\s*(\d+)%\)/);
  if (!match) return `rgba(74, 144, 217, ${alpha})`;
  const h = parseInt(match[1]) / 360;
  const s = parseInt(match[2]) / 100;
  const l = parseInt(match[3]) / 100;

  let r: number, g: number, b: number;
  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p: number, q: number, t: number) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1/6) return p + (q - p) * 6 * t;
      if (t < 1/2) return q;
      if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1/3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1/3);
  }

  return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${alpha})`;
}

/**
 * Format run inputs/params as a compact key=value string.
 *
 * Returns at most `max` key=value pairs, joined by ", ".
 * Long values are truncated.
 */
export function formatParams(inputs: Record<string, unknown>, max: number = 2): string {
  const entries = Object.entries(inputs || {});
  if (entries.length === 0) return '';
  return entries.slice(0, max).map(([k, v]) => {
    const vs = typeof v === 'object' ? JSON.stringify(v) : String(v);
    const truncated = vs.length > 20 ? vs.slice(0, 17) + '...' : vs;
    return `${k}=${truncated}`;
  }).join(', ');
}
