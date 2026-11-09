/**
 * The version menu's rules, kept apart from the components so they are
 * tested as plain functions.
 *
 * - A canvas load and the last-seen version (request decisions 9, 15): a
 *   first-ever load records the installed version silently; a changed
 *   version opens What's new once, for every version since the last one this
 *   browser saw, and records the new one.
 * - The dot (decision 22): it shows while an offer is out that the menu was
 *   never opened for; opening the menu records the offered version as seen.
 * - What the menu counts (Known issues in <installed>) and how a changelog
 *   line's markdown is drawn: inline code, bold and http(s) links only, as
 *   tokens the component renders (no HTML is ever injected).
 */
import type { AboutFooter, AboutResponse, ChangelogVersion } from './api.js';

/** localStorage key: the wfc version this browser saw last. */
export const LAST_SEEN_KEY = 'wfc.version.lastSeen';
/** localStorage key: the offered version the menu was last opened for. */
export const SEEN_OFFER_KEY = 'wfc.version.seenOffer';

/** The changelog heading the menu's Known issues line counts. */
export const KNOWN_ISSUES_HEADING = 'Known issues';

/** What a load does: the version to record, and What's new's range (or none). */
export interface Arrival {
  record: string | null;
  whatsNewSince: string | null;
}

/**
 * What a canvas load does with the version this browser saw last.
 *
 * Args:
 *     lastSeen: The stored last-seen version, or null on a first-ever load.
 *     installed: The installed version the about route answered, or null.
 *
 * Returns:
 *     `record`, the version to store (null: store nothing), and
 *     `whatsNewSince`, the version What's new opens since (null: it stays
 *     closed).
 */
export function arrival(lastSeen: string | null, installed: string | null): Arrival {
  if (!installed || lastSeen === installed) return { record: null, whatsNewSince: null };
  if (lastSeen === null) return { record: installed, whatsNewSince: null };
  return { record: installed, whatsNewSince: lastSeen };
}

/** Whether the label shows its dot: an offer the menu was not opened for. */
export function showsDot(offer: { version: string } | null, seenOffer: string | null): boolean {
  return offer !== null && offer.version !== seenOffer;
}

/** The offered version to record as seen when the menu opens, or null for none. */
export function offerToRecord(offer: { version: string } | null, seenOffer: string | null): string | null {
  return offer && offer.version !== seenOffer ? offer.version : null;
}

/** How many items a version's Known issues section lists (0 when it has none). */
export function knownIssuesCount(version: ChangelogVersion | undefined): number {
  const sec = version?.sections.find(s => s.heading === KNOWN_ISSUES_HEADING);
  return sec ? sec.items.length : 0;
}

/**
 * The versions What's new lists after an upgrade.
 *
 * Args:
 *     since: The about route's changelog for `?since=<last-seen>`.
 *     installed: Its changelog for the installed version alone.
 *
 * Returns:
 *     Every version since the last-seen one, newest first; the installed
 *     version when there is none newer (a downgrade).
 */
export function whatsNewVersions(since: ChangelogVersion[], installed: ChangelogVersion[]): ChangelogVersion[] {
  return since.length > 0 ? since : installed;
}

/** Whether a parsed body has the about route's shape. */
export function isAboutAnswer(body: unknown): body is AboutResponse {
  if (!body || typeof body !== 'object') return false;
  const b = body as Record<string, unknown>;
  const changelog = b.changelog as Record<string, unknown> | undefined;
  const footer = b.footer as Record<string, unknown> | undefined;
  return 'version' in b && 'offer' in b
    && !!changelog && Array.isArray(changelog.versions)
    && !!footer && typeof footer.text === 'string'
    && !!b.links && typeof b.links === 'object';
}

/** One run of a changelog line: plain text, inline code, bold, or a link. */
export type InlineToken =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'bold'; text: string }
  | { kind: 'link'; text: string; href: string };

const INLINE_RE = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

/**
 * Split a changelog line into the runs the canvas draws.
 *
 * Args:
 *     text: One item, intro or sentence, as markdown.
 *
 * Returns:
 *     Its tokens in order; adjacent text runs are merged. A link whose
 *     target is not http(s) keeps only its text.
 */
export function inlineMarkdown(text: string): InlineToken[] {
  const out: InlineToken[] = [];
  const pushText = (t: string) => {
    if (!t) return;
    const last = out[out.length - 1];
    if (last && last.kind === 'text') last.text += t;
    else out.push({ kind: 'text', text: t });
  };
  let at = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    pushText(text.slice(at, m.index));
    if (m[1] !== undefined) out.push({ kind: 'code', text: m[1] });
    else if (m[2] !== undefined) out.push({ kind: 'bold', text: m[2] });
    else if (/^https?:\/\//.test(m[4])) out.push({ kind: 'link', text: m[3], href: m[4] });
    else pushText(m[3]);
    at = (m.index ?? 0) + m[0].length;
  }
  pushText(text.slice(at));
  return out;
}

/** The trailing words of the footer's sentence that link the `[updates]` guide section. */
export const FOOTER_LINK_TEXT = 'wf-canvas.toml';

/**
 * Split the footer into its sentence and the trailing link.
 *
 * Returns:
 *     `before`, the sentence's runs, and `link`, the text that links the
 *     guide's `[updates]` section (null for an invalid setting's refusal,
 *     which shows as it is).
 */
export function footerParts(footer: AboutFooter): { before: InlineToken[]; link: string | null } {
  if (footer.state !== 'invalid' && footer.text.endsWith(FOOTER_LINK_TEXT)) {
    return {
      before: inlineMarkdown(footer.text.slice(0, -FOOTER_LINK_TEXT.length)),
      link: FOOTER_LINK_TEXT,
    };
  }
  return { before: [{ kind: 'text', text: footer.text }], link: null };
}
