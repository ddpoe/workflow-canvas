/**
 * The Descendants view's card zoom: a percentage, or Fit.
 *
 * Fit widens the cards until the widest reaches the view's right edge, never
 * below plain size or above half again. The − and + steps move in tens
 * within a wider range. The choice is remembered in this browser (a
 * per-viewer convenience: a storage that refuses only loses the memory).
 */

/** The narrowest and widest a chosen zoom may be, in percent. */
export const ZOOM_MIN = 50;
export const ZOOM_MAX = 200;
/** How far one − or + moves the zoom, in percent. */
export const ZOOM_STEP = 10;
/** The range Fit stays within, in percent. */
export const FIT_MIN = 100;
export const FIT_MAX = 150;

/** localStorage key holding the choice: `fit` or a whole percentage. */
export const ZOOM_KEY = 'wfc.history.descendantsZoom';

/** What the view is set to: Fit, or a fixed percentage. */
export type ZoomChoice = 'fit' | number;

/**
 * The Fit zoom for the cards' measured width.
 *
 * Args:
 *     available: Width from the rows' left edge to the view's right edge, in pixels.
 *     natural: Width from the rows' left edge to the widest card's right edge
 *         at 100%, in pixels; 0 when nothing could be measured.
 *
 * Returns:
 *     A whole percentage within FIT_MIN..FIT_MAX, rounded down so the widest
 *     card stays inside the view.
 */
export function fitZoom(available: number, natural: number): number {
  if (!(natural > 0) || !(available > 0)) return FIT_MIN;
  const p = Math.floor((available / natural) * 100);
  return Math.min(FIT_MAX, Math.max(FIT_MIN, p));
}

function clampZoom(p: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(p)));
}

/** One + step from a percentage: the next multiple of ten, at most ZOOM_MAX. */
export function zoomIn(p: number): number {
  return clampZoom(Math.floor(p / ZOOM_STEP) * ZOOM_STEP + ZOOM_STEP);
}

/** One − step from a percentage: the previous multiple of ten, at least ZOOM_MIN. */
export function zoomOut(p: number): number {
  return clampZoom(Math.ceil(p / ZOOM_STEP) * ZOOM_STEP - ZOOM_STEP);
}

/**
 * The choice remembered in this browser.
 *
 * Returns:
 *     `fit` when nothing usable is kept (the first visit, a refused storage,
 *     an unreadable value); otherwise the kept percentage, within bounds.
 */
export function loadZoomChoice(): ZoomChoice {
  try {
    const v = globalThis.localStorage?.getItem(ZOOM_KEY);
    if (v === null || v === undefined || v === 'fit') return 'fit';
    const n = Number(v);
    return Number.isFinite(n) && v.trim() !== '' ? clampZoom(n) : 'fit';
  } catch {
    return 'fit';
  }
}

/** Remember the choice in this browser. */
export function saveZoomChoice(choice: ZoomChoice): void {
  try {
    globalThis.localStorage?.setItem(ZOOM_KEY, String(choice));
  } catch {
    // Storage refused (private window, blocked site data): the zoom is not remembered.
  }
}
