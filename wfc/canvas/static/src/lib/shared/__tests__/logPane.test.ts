/**
 * The log box every Output tab shows.
 *
 * A run's stdout and stderr in a box of a fixed height, with a bar above it
 * that counts the lines and copies the whole log. The box is short until the
 * user asks for more, and offers more only when the log is longer than the
 * box; it never grows past its open height, it scrolls instead.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/svelte';
import LogPane from '../LogPane.svelte';

type Line = { kind: 'stdout' | 'stderr'; line: string };

const LINES: Line[] = [
  { kind: 'stdout', line: 'reading counts.parquet' },
  { kind: 'stderr', line: 'Traceback (most recent call last):' },
  { kind: 'stderr', line: "KeyError: 'gene_symbol'" },
];

/** jsdom lays nothing out: give every <pre> a content height and a box height. */
function fakeHeights(content: number, box: number): void {
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(content);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(box);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('LogPane', () => {
  it('shows each line, stderr in its own style, and counts them', () => {
    const view = render(LogPane, { props: { lines: LINES } });
    const pre = view.getByTestId('log-pane');
    expect(pre.textContent).toContain('reading counts.parquet');
    expect(pre.querySelectorAll('.log-stderr')).toHaveLength(2);
    expect(view.getByTestId('log-count').textContent).toBe('3 lines');
  });

  it('copies the whole log as plain text and says so', async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const view = render(LogPane, { props: { lines: LINES } });
    await fireEvent.click(view.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith(
      "reading counts.parquet\nTraceback (most recent call last):\nKeyError: 'gene_symbol'",
    );
    expect(await view.findByRole('button', { name: '✓ Copied' })).toBeTruthy();
  });

  it('offers no Show more when the log fits the short box', () => {
    fakeHeights(100, 100);
    const view = render(LogPane, { props: { lines: LINES } });
    expect(view.queryByRole('button', { name: /Show more/ })).toBeNull();
  });

  it('a log longer than the box opens to a scrolling box and closes again', async () => {
    fakeHeights(900, 200);
    const view = render(LogPane, { props: { lines: LINES } });
    const pre = view.getByTestId('log-pane');
    expect(pre.classList.contains('open')).toBe(false);

    await fireEvent.click(await view.findByRole('button', { name: /Show more/ }));
    expect(pre.classList.contains('open')).toBe(true);

    await fireEvent.click(view.getByRole('button', { name: /Show less/ }));
    expect(pre.classList.contains('open')).toBe(false);
  });

  it('a finished log opens at its first line; a live one follows its newest', () => {
    fakeHeights(900, 200);
    const done = render(LogPane, { props: { lines: LINES } });
    expect(done.getByTestId('log-pane').scrollTop).toBe(0);
    cleanup();
    const live = render(LogPane, { props: { lines: LINES, live: true } });
    expect(live.getByTestId('log-pane').scrollTop).toBe(900);
  });

  it('a live log stops following once the reader scrolls up, and has no fade', async () => {
    fakeHeights(900, 200);
    const view = render(LogPane, { props: { lines: LINES, live: true } });
    const pre = view.getByTestId('log-pane');
    expect(pre.parentElement!.classList.contains('fade')).toBe(false);

    pre.scrollTop = 100;               // the reader scrolls up to read
    await fireEvent.scroll(pre);
    await view.rerender({ lines: [...LINES, { kind: 'stdout', line: 'one more' }], live: true });
    expect(pre.scrollTop).toBe(100);   // left where they are
  });

  it('a live log resumes following once the reader scrolls back to the bottom', async () => {
    fakeHeights(900, 200);
    const view = render(LogPane, { props: { lines: LINES, live: true } });
    const pre = view.getByTestId('log-pane');
    pre.scrollTop = 100;
    await fireEvent.scroll(pre);
    pre.scrollTop = 700;               // back at the bottom: 900 - 700 - 200 = 0
    await fireEvent.scroll(pre);
    await view.rerender({ lines: [...LINES, { kind: 'stdout', line: 'one more' }], live: true });
    expect(pre.scrollTop).toBe(900);
  });

  it('a followed log goes back to its first line when the run finishes', async () => {
    fakeHeights(900, 200);
    const view = render(LogPane, { props: { lines: LINES, live: true } });
    const pre = view.getByTestId('log-pane');
    expect(pre.scrollTop).toBe(900);
    await view.rerender({ lines: LINES, live: false });
    expect(pre.scrollTop).toBe(0);
  });

  it('Show less goes back to the first line of a finished log, the newest of a live one', async () => {
    fakeHeights(900, 200);
    const done = render(LogPane, { props: { lines: LINES } });
    await fireEvent.click(await done.findByRole('button', { name: /Show more/ }));
    const pre = done.getByTestId('log-pane');
    pre.scrollTop = 400;
    await fireEvent.click(done.getByRole('button', { name: /Show less/ }));
    expect(pre.scrollTop).toBe(0);
    cleanup();

    const live = render(LogPane, { props: { lines: LINES, live: true } });
    await fireEvent.click(await live.findByRole('button', { name: /Show more/ }));
    const lpre = live.getByTestId('log-pane');
    lpre.scrollTop = 100;              // scrolled up to read while open
    await fireEvent.scroll(lpre);
    await fireEvent.click(live.getByRole('button', { name: /Show less/ }));
    expect(lpre.scrollTop).toBe(900);
    await live.rerender({ lines: [...LINES, { kind: 'stdout', line: 'more' }], live: true });
    expect(lpre.scrollTop).toBe(900);  // following again
  });

  it('marks Show more as expanded when open', async () => {
    fakeHeights(900, 200);
    const view = render(LogPane, { props: { lines: LINES } });
    const more = await view.findByRole('button', { name: /Show more/ });
    expect(more.getAttribute('aria-expanded')).toBe('false');
    await fireEvent.click(more);
    expect(view.getByRole('button', { name: /Show less/ }).getAttribute('aria-expanded')).toBe('true');
  });

  it('shows a note beside the count', () => {
    const view = render(LogPane, { props: { lines: [], note: 'Connection lost', noteTone: 'failed' } });
    const note = view.getByTestId('log-note');
    expect(note.textContent).toBe('Connection lost');
    expect(note.classList.contains('failed')).toBe(true);
  });
});
