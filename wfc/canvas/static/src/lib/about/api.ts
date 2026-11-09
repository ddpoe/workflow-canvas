/**
 * Network calls for the version menu: the about route and the bug report.
 *
 * `GET /api/about` answers the installed wfc, the update offer, the
 * changelog, the footer and the guide links; `?since=<v>` makes its
 * changelog every version newer than `<v>` up to the installed one.
 * `GET /api/about/report` runs the Docker probe and answers the prefilled
 * new-issue URL. The about call never throws: a failed or malformed answer
 * is `null`, which the menu treats as no answer (no label, no dot, no error).
 */
import type { components } from '../types/api.js';
import { isAboutAnswer } from './versionMenu.js';

export type AboutResponse = components['schemas']['AboutResponse'];
export type ChangelogVersion = components['schemas']['ChangelogVersion'];
export type ChangelogSection = components['schemas']['ChangelogSection'];
export type UpdateOffer = components['schemas']['UpdateOffer'];
export type AboutFooter = components['schemas']['AboutFooter'];

/**
 * Ask the about route.
 *
 * Args:
 *     since: The version this browser saw last, for What's new after an
 *         upgrade; omitted, the changelog holds the installed version alone.
 *
 * Returns:
 *     The answer, or `null` when the server did not answer it.
 */
export async function fetchAbout(since: string | null = null): Promise<AboutResponse | null> {
  const url = since ? `/api/about?since=${encodeURIComponent(since)}` : '/api/about';
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const body: unknown = await res.json();
    return isAboutAnswer(body) ? body : null;
  } catch {
    return null;
  }
}

/**
 * Ask for the prefilled new-issue URL Report a bug opens.
 *
 * Returns:
 *     The URL.
 *
 * Raises:
 *     Error: The server did not answer one.
 */
export async function fetchBugReportUrl(): Promise<string> {
  const res = await fetch('/api/about/report');
  if (!res.ok) throw new Error(`the report route answered ${res.status}`);
  const body = (await res.json()) as components['schemas']['BugReportResponse'];
  if (typeof body?.url !== 'string') throw new Error('the report route answered no URL');
  return body.url;
}
