/**
 * The version menu's state over successive canvas loads: the last-seen rule
 * applied to the about route's answers, with fetch faked and the browser
 * storage held in memory.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { about, closeWhatsNew, menuOpened, openWhatsNew, refreshAbout, seenOffer,
         whatsNew, type VersionStorage } from '../aboutStore.js';
import { LAST_SEEN_KEY, SEEN_OFFER_KEY } from '../versionMenu.js';

function memory(init: Record<string, string> = {}): VersionStorage & { data: Record<string, string> } {
  const data = { ...init };
  return { data, getItem: k => data[k] ?? null, setItem: (k, v) => { data[k] = v; } };
}

const v = (version: string) => ({
  version, date: '2026-11-08', sections: [{ heading: 'Upgrading', intro: '', items: [`about ${version}`] }],
});

function answer(version: string, opts: { offer?: string; since?: string[] } = {}) {
  return {
    version,
    offer: opts.offer ? {
      version: opts.offer, prerelease: false, command: 'pip install -U workflow-canvas',
      notes: { sections: [], link: 'https://example.org/changelog' },
    } : null,
    changelog: { versions: (opts.since ?? [version]).map(v), link: null },
    footer: { state: 'stable', text: 'Stable releases only. For betas, set `prereleases = true` in wf-canvas.toml' },
    links: { docs: 'd', known_issues: 'k', updates_guide: 'u', plugin_install: 'p', changelog: 'c' },
  };
}

/** Serve `/api/about` (and `?since=`) from a function of the requested URL. */
function serve(fn: (url: string) => unknown) {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(url);
    const body = fn(url);
    if (body === undefined) return new Response('down', { status: 502 });
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  }));
  return calls;
}

beforeEach(() => {
  about.set(null);
  seenOffer.set(null);
  closeWhatsNew();
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('a canvas load', () => {
  it('records the installed version silently the first time, and opens nothing', async () => {
    const store = memory();
    const calls = serve(() => answer('0.8.0b4'));
    await refreshAbout(store);
    expect(store.data[LAST_SEEN_KEY]).toBe('0.8.0b4');
    expect(get(whatsNew)).toBeNull();
    expect(calls).toEqual(['/api/about']);
  });

  it('opens What\'s new once after an upgrade, with every version since the last-seen one', async () => {
    const store = memory({ [LAST_SEEN_KEY]: '0.8.0b2' });
    const calls = serve(url => url.includes('since=0.8.0b2')
      ? answer('0.8.0b4', { since: ['0.8.0b4', '0.8.0b3'] })
      : answer('0.8.0b4'));
    await refreshAbout(store);
    const view = get(whatsNew);
    expect(view?.title).toBe("What's new since 0.8.0b2");
    expect(view?.versions.map(x => x.version)).toEqual(['0.8.0b4', '0.8.0b3']);
    expect(store.data[LAST_SEEN_KEY]).toBe('0.8.0b4');
    expect(calls).toEqual(['/api/about', '/api/about?since=0.8.0b2']);

    // The next load (a reload, or the tab shown again) does not open it again.
    closeWhatsNew();
    await refreshAbout(store);
    expect(get(whatsNew)).toBeNull();
  });

  it('keeps the last answer and shows nothing when the route fails', async () => {
    const store = memory();
    serve(() => undefined);
    await refreshAbout(store);
    expect(get(about)).toBeNull();
    expect(store.data[LAST_SEEN_KEY]).toBeUndefined();

    serve(() => answer('0.8.0b4'));
    await refreshAbout(store);
    serve(() => undefined);
    await refreshAbout(store);
    expect(get(about)?.version).toBe('0.8.0b4');
  });
});

describe('opening the menu', () => {
  it('records the offered version as seen, so the dot goes until a newer offer', async () => {
    const store = memory({ [LAST_SEEN_KEY]: '0.8.0' });
    serve(() => answer('0.8.0', { offer: '0.8.1' }));
    await refreshAbout(store);
    expect(get(seenOffer)).toBeNull();
    menuOpened(store);
    expect(store.data[SEEN_OFFER_KEY]).toBe('0.8.1');
    expect(get(seenOffer)).toBe('0.8.1');

    // A later load reads the seen offer back.
    seenOffer.set(null);
    await refreshAbout(store);
    expect(get(seenOffer)).toBe('0.8.1');
  });

  it('opens What\'s new for the installed version alone, at Known issues from that line', async () => {
    const store = memory({ [LAST_SEEN_KEY]: '0.8.0b4' });
    serve(() => answer('0.8.0b4'));
    await refreshAbout(store);
    openWhatsNew(true);
    expect(get(whatsNew)).toMatchObject({ title: "What's new in 0.8.0b4", atKnownIssues: true });
    expect(get(whatsNew)?.versions.map(x => x.version)).toEqual(['0.8.0b4']);
  });
});
