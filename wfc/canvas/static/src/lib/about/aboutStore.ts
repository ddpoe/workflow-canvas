/**
 * The version menu's state: the about route's answer, the offer the menu was
 * last opened for, and the What's new dialog.
 *
 * The page asks `/api/about` on load and whenever the tab becomes visible
 * again (`refreshAbout`). A failed ask keeps the last answer (none on a
 * first load: the label stays hidden) and shows no error. Each answer goes
 * through the last-seen rule (`versionMenu.arrival`): a changed installed
 * version opens What's new once, for every version since the last-seen one,
 * fetched with `?since=`. The two versions live in localStorage.
 */
import { get, writable } from 'svelte/store';
import { fetchAbout, type AboutResponse, type ChangelogVersion } from './api.js';
import {
  arrival,
  LAST_SEEN_KEY,
  offerToRecord,
  SEEN_OFFER_KEY,
  whatsNewVersions,
} from './versionMenu.js';

/** The storage the two versions are kept in (localStorage in the page). */
export interface VersionStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** What the What's new dialog shows. */
export interface WhatsNewView {
  title: string;
  versions: ChangelogVersion[];
  /** The Changelog link, shown alone when there are no versions. */
  link: string | null;
  /** Opened from the menu's Known issues line: scrolled to Known issues. */
  atKnownIssues: boolean;
}

/** The about route's last answer; null until it first answers. */
export const about = writable<AboutResponse | null>(null);
/** The offered version the menu was last opened for. */
export const seenOffer = writable<string | null>(null);
/** The What's new dialog, or null when it is closed. */
export const whatsNew = writable<WhatsNewView | null>(null);

function pageStorage(): VersionStorage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

function read(storage: VersionStorage | null, key: string): string | null {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(storage: VersionStorage | null, key: string, value: string): void {
  try {
    storage?.setItem(key, value);
  } catch {
    // Storage refused (private mode, quota): the rule just repeats next load.
  }
}

let inFlight: Promise<void> | null = null;

/**
 * Ask the about route and apply the last-seen rule to its answer.
 *
 * Args:
 *     storage: Where the versions are kept; the page's localStorage by default.
 */
export function refreshAbout(storage: VersionStorage | null = pageStorage()): Promise<void> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    seenOffer.set(read(storage, SEEN_OFFER_KEY));
    const answer = await fetchAbout();
    if (!answer) return;
    about.set(answer);
    const step = arrival(read(storage, LAST_SEEN_KEY), answer.version);
    if (step.record) write(storage, LAST_SEEN_KEY, step.record);
    if (step.whatsNewSince) {
      const since = await fetchAbout(step.whatsNewSince);
      const versions = whatsNewVersions(since?.changelog.versions ?? [], answer.changelog.versions);
      whatsNew.set({
        title: `What's new since ${step.whatsNewSince}`,
        versions,
        link: versions.length ? null : answer.changelog.link ?? answer.links.changelog,
        atKnownIssues: false,
      });
    }
  })().finally(() => { inFlight = null; });
  return inFlight;
}

/**
 * The menu opened: record the offered version as seen, so the dot goes
 * until a newer offer.
 */
export function menuOpened(storage: VersionStorage | null = pageStorage()): void {
  const answer = get(about);
  const record = offerToRecord(answer?.offer ?? null, get(seenOffer));
  if (!record) return;
  write(storage, SEEN_OFFER_KEY, record);
  seenOffer.set(record);
}

/** Open What's new from the menu: the installed version only, at Known issues when asked. */
export function openWhatsNew(atKnownIssues = false): void {
  const answer = get(about);
  if (!answer) return;
  whatsNew.set({
    title: `What's new in ${answer.version ?? 'this version'}`,
    versions: answer.changelog.versions,
    link: answer.changelog.versions.length ? null : answer.changelog.link ?? answer.links.changelog,
    atKnownIssues,
  });
}

/** Close the What's new dialog. */
export function closeWhatsNew(): void {
  whatsNew.set(null);
}
