/**
 * Placement and dismissal for the Registry's floating menus and cards.
 *
 * A floating element is fixed-position, placed from the box of the element
 * that opened it (its anchor), so no scroll container clips it, and kept
 * inside the window. It closes on Escape or a click outside both itself and
 * its anchor; a click on the anchor is left to the anchor, which toggles.
 */

/** Where a floating element sits against its anchor. */
export type Align = 'right' | 'left' | 'side';

/**
 * The fixed position of a floating element of size `w` x `h`: under its
 * anchor, right edges aligned (`right`) or left edges (`left`), or beside it
 * (`side`); kept 8px inside the window, and above the anchor when there is
 * no room below.
 */
export function floatingPosition(anchor: DOMRect, w: number, h: number, align: Align,
                                 view = { width: window.innerWidth, height: window.innerHeight }
                                 ): { left: number; top: number } {
  let left = align === 'right' ? anchor.right - w : align === 'side' ? anchor.right + 6 : anchor.left;
  let top = align === 'side' ? anchor.top - 4 : anchor.bottom + 4;
  left = Math.max(8, Math.min(left, view.width - w - 8));
  if (top + h > view.height - 8) {
    top = Math.max(8, (align === 'side' ? anchor.bottom : anchor.top - 4) - h);
  }
  return { left, top };
}

/**
 * Keep a floating element placed and closable while it is shown: it is
 * placed now and again on resize and scroll, and `onClose` runs on Escape
 * or a click outside the element and its anchor.
 *
 * Returns:
 *     The function that removes the listeners.
 */
export function attachFloating(el: HTMLElement, anchor: HTMLElement, align: Align,
                               setPos: (pos: { left: number; top: number }) => void,
                               onClose: () => void): () => void {
  const place = () => {
    if (!anchor.isConnected) return;
    setPos(floatingPosition(anchor.getBoundingClientRect(), el.offsetWidth, el.offsetHeight, align));
  };
  const outside = (e: Event) => {
    const t = e.target as Node | null;
    if (t && (el.contains(t) || anchor.contains(t))) return;
    onClose();
  };
  const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
  place();
  window.addEventListener('click', outside, true);
  window.addEventListener('keydown', key);
  window.addEventListener('resize', place);
  window.addEventListener('scroll', place, true);
  return () => {
    window.removeEventListener('click', outside, true);
    window.removeEventListener('keydown', key);
    window.removeEventListener('resize', place);
    window.removeEventListener('scroll', place, true);
  };
}
