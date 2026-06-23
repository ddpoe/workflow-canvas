/**
 * Lazy-loaded Shiki highlighter for read-only code blocks.
 *
 * Shiki adds ~150KB for the core + one theme + a few langs. We only pay that
 * cost on first use (first time a user expands a method row to view files).
 * The highlighter is cached module-level so every subsequent highlight is
 * synchronous after the first await.
 */

import type { Highlighter } from 'shiki';

let _highlighterPromise: Promise<Highlighter> | null = null;

const SUPPORTED_LANGS = ['python', 'yaml', 'json', 'toml', 'markdown', 'shell', 'text'];

export async function getHighlighter(): Promise<Highlighter> {
  if (!_highlighterPromise) {
    _highlighterPromise = (async () => {
      const { createHighlighter } = await import('shiki');
      return await createHighlighter({
        themes: ['dark-plus'],
        langs: SUPPORTED_LANGS.filter(l => l !== 'text'), // 'text' is fallback, no lang file
      });
    })();
  }
  return _highlighterPromise;
}

/** Returns an HTML string with Shiki's token-span markup. Safe to @html-render. */
export async function highlight(code: string, lang: string): Promise<string> {
  const safeLang = SUPPORTED_LANGS.includes(lang) ? lang : 'text';
  if (safeLang === 'text') {
    // No highlighter lang; escape and wrap in a <pre><code> skeleton that
    // matches Shiki's default structure so our styles still apply.
    const escaped = code
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return `<pre class="shiki"><code>${escaped}</code></pre>`;
  }
  const hl = await getHighlighter();
  return hl.codeToHtml(code, { lang: safeLang, theme: 'dark-plus' });
}
