/**
 * The version menu's rules, as pure logic: what a canvas load does with the
 * version this browser saw last (request decisions 9 and 15), when the label
 * shows its dot (decision 22), what the menu counts and links, and how a
 * changelog line's markdown and the footer's sentence are drawn.
 */
import { describe, expect, it } from 'vitest';
import {
  arrival,
  footerParts,
  inlineMarkdown,
  isAboutAnswer,
  knownIssuesCount,
  offerToRecord,
  showsDot,
  whatsNewVersions,
} from '../versionMenu.js';
import type { ChangelogVersion } from '../api.js';

function entry(version: string, sections: [string, string[]][]): ChangelogVersion {
  return {
    version, date: '2026-11-08',
    sections: sections.map(([heading, items]) => ({ heading, intro: '', items })),
  };
}

describe('a canvas load and the version this browser saw last', () => {
  it('records the installed version silently on a first-ever load', () => {
    expect(arrival(null, '0.8.0b4')).toEqual({ record: '0.8.0b4', whatsNewSince: null });
  });

  it('does nothing when the version is the one seen last', () => {
    expect(arrival('0.8.0b4', '0.8.0b4')).toEqual({ record: null, whatsNewSince: null });
  });

  it('opens What\'s new since the last-seen version after an upgrade, and records the new one', () => {
    expect(arrival('0.8.0b2', '0.8.0b4')).toEqual({ record: '0.8.0b4', whatsNewSince: '0.8.0b2' });
  });

  it('records nothing and opens nothing when the installed version is unknown', () => {
    expect(arrival('0.8.0b2', null)).toEqual({ record: null, whatsNewSince: null });
    expect(arrival(null, null)).toEqual({ record: null, whatsNewSince: null });
  });
});

describe('the dot and the seen offer', () => {
  const offer = { version: '0.8.1' };

  it('shows the dot for an offer the menu was never opened for', () => {
    expect(showsDot(offer, null)).toBe(true);
  });

  it('hides it once the menu was opened for that version, and brings it back for a newer one', () => {
    expect(showsDot(offer, '0.8.1')).toBe(false);
    expect(showsDot({ version: '0.8.2' }, '0.8.1')).toBe(true);
  });

  it('never shows it with no offer', () => {
    expect(showsDot(null, null)).toBe(false);
    expect(showsDot(null, '0.8.1')).toBe(false);
  });

  it('records the offered version when the menu opens, and nothing when it is already recorded or there is none', () => {
    expect(offerToRecord(offer, null)).toBe('0.8.1');
    expect(offerToRecord(offer, '0.8.0')).toBe('0.8.1');
    expect(offerToRecord(offer, '0.8.1')).toBeNull();
    expect(offerToRecord(null, '0.8.1')).toBeNull();
  });
});

describe('what the menu and the dialog show', () => {
  it('counts the items of the installed version\'s Known issues', () => {
    const v = entry('0.8.0b4', [['Upgrading', ['a']], ['Added', ['b', 'c']], ['Known issues', ['x', 'y', 'z']]]);
    expect(knownIssuesCount(v)).toBe(3);
    expect(knownIssuesCount(entry('0.8.0b4', [['Added', ['b']]]))).toBe(0);
    expect(knownIssuesCount(undefined)).toBe(0);
  });

  it('shows every version since the last-seen one after an upgrade, else the installed one', () => {
    const installed = [entry('0.8.0b4', [])];
    const since = [entry('0.8.0b4', []), entry('0.8.0b3', [])];
    expect(whatsNewVersions(since, installed).map(v => v.version)).toEqual(['0.8.0b4', '0.8.0b3']);
    // A downgrade (nothing newer than the last-seen version) shows the installed version.
    expect(whatsNewVersions([], installed).map(v => v.version)).toEqual(['0.8.0b4']);
  });

  it('accepts only an answer shaped like the about route\'s', () => {
    expect(isAboutAnswer({})).toBe(false);
    expect(isAboutAnswer(null)).toBe(false);
    expect(isAboutAnswer({
      version: '0.8.0b4', offer: null, changelog: { versions: [], link: 'x' },
      footer: { state: 'stable', text: 't' }, links: { docs: 'd' },
    })).toBe(true);
  });
});

describe('a changelog line\'s markdown', () => {
  it('draws inline code, bold and links, and keeps everything else as text', () => {
    expect(inlineMarkdown('Run `wfc doctor` once, **then** see [the guide](https://x.org/g).')).toEqual([
      { kind: 'text', text: 'Run ' },
      { kind: 'code', text: 'wfc doctor' },
      { kind: 'text', text: ' once, ' },
      { kind: 'bold', text: 'then' },
      { kind: 'text', text: ' see ' },
      { kind: 'link', text: 'the guide', href: 'https://x.org/g' },
      { kind: 'text', text: '.' },
    ]);
  });

  it('keeps a link that is not http(s) as its text', () => {
    expect(inlineMarkdown('see [x](javascript:void)')).toEqual([{ kind: 'text', text: 'see x' }]);
  });

  it('keeps an unclosed backtick as text', () => {
    expect(inlineMarkdown('a ` b')).toEqual([{ kind: 'text', text: 'a ` b' }]);
  });
});

describe('the footer', () => {
  it('links the sentence\'s trailing wf-canvas.toml to the [updates] guide section', () => {
    const parts = footerParts({ state: 'stable', text: 'Stable releases only. For betas, set `prereleases = true` in wf-canvas.toml' });
    expect(parts.link).toBe('wf-canvas.toml');
    expect(parts.before).toEqual([
      { kind: 'text', text: 'Stable releases only. For betas, set ' },
      { kind: 'code', text: 'prereleases = true' },
      { kind: 'text', text: ' in ' },
    ]);
  });

  it('shows an invalid setting\'s refusal as it is, with no link', () => {
    const text = "wf-canvas.toml [updates] check must be true or false, not 'yes'; set check = true";
    const parts = footerParts({ state: 'invalid', text });
    expect(parts.link).toBeNull();
    expect(parts.before).toEqual([{ kind: 'text', text }]);
  });
});
