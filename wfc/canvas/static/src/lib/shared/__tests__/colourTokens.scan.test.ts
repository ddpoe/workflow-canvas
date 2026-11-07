/**
 * The protected colours live in one place (canvas-ui case 19).
 *
 * The SOP `docs/sops/canvas-colours` reserves a set of colours for run
 * status, controls and system nodes, and names a token for each. A source
 * scan of `src/` finds every spelling of a protected colour, as a hex
 * literal or as an `rgb()`/`rgba()` triplet, and allows it only as the
 * value of its token in `app.css`'s `:root`. Every other file reaches the
 * colour through `var(--token)`, so the colour is changed in one line.
 *
 * The hex values below are written without their `#` so this file does not
 * match its own scan.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, relative, resolve } from 'node:path';

// This file's directory: vite-node injects `__dirname`; fall back to the
// module URL where it does not.
const HERE = typeof __dirname === 'string'
  ? __dirname
  : fileURLToPath(new URL('.', import.meta.url));
const SRC = resolve(HERE, '../../..');
const APP_CSS = join(SRC, 'app.css');

/** SOP Protected Colours: token -> hex (without `#`). */
const PROTECTED: Record<string, string> = {
  '--status-ok': '50E7A3',
  '--status-active': 'F1B620',
  '--status-failed': 'F97163',
  '--status-failed-ink': 'FF8A7E',
  '--status-cancelled': '7F8EA3',
  '--status-cancelled-ink': 'A3B2C8',
  '--status-cached': 'EAB8FF',
  '--accent': '4A90D9',
  '--fill-system': '0D857C',
  '--system-ink': '5ECBCB',
};

/**
 * Colours the standard retired, with the token that replaced them: the old
 * system teal is now `--system-ink` / `--fill-system`.
 */
const RETIRED: Record<string, string> = {
  'the retired system teal (use --system-ink)': '1ABC9C',
};

function rgbOf(hex: string): [number, number, number] {
  return [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}

/** Every way a protected colour can be spelled in a source file. */
function spellings(hex: string): RegExp[] {
  const [r, g, b] = rgbOf(hex);
  return [
    // #RRGGBB, or #RRGGBBAA with an alpha, but not a longer run of digits.
    new RegExp(`#${hex}([0-9a-f]{2})?(?![0-9a-f])`, 'i'),
    new RegExp(`rgba?\\(\\s*${r}\\s*,\\s*${g}\\s*,\\s*${b}\\s*[,)]`),
  ];
}

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sourceFiles(path));
    else if (/\.(svelte|ts|js|css|html)$/.test(name)) out.push(path);
  }
  return out;
}

/** `app.css` with its `:root { ... }` block blanked out. */
function appCssOutsideRoot(css: string): string {
  return css.replace(/:root\s*\{[^}]*\}/, '');
}

function rootBlock(css: string): string {
  const m = css.match(/:root\s*\{([^}]*)\}/);
  return m ? m[1] : '';
}

describe('protected colours are spelled only as their tokens', () => {
  it('app.css :root declares each protected token with its SOP value', () => {
    const root = rootBlock(readFileSync(APP_CSS, 'utf8'));
    for (const [token, hex] of Object.entries(PROTECTED)) {
      const decl = new RegExp(`${token}\\s*:\\s*#${hex}\\s*;`, 'i');
      expect(root, `${token} should be #${hex}`).toMatch(decl);
    }
  });

  it('no file under src/ spells a protected colour outside the tokens', () => {
    const hits: string[] = [];
    for (const file of sourceFiles(SRC)) {
      let text = readFileSync(file, 'utf8');
      if (file === APP_CSS) text = appCssOutsideRoot(text);
      const lines = text.split('\n');
      lines.forEach((line, i) => {
        for (const [token, hex] of Object.entries(PROTECTED)) {
          if (spellings(hex).some(re => re.test(line))) {
            hits.push(`${relative(SRC, file)}:${i + 1} spells ${token}: ${line.trim()}`);
          }
        }
      });
    }
    expect(hits).toEqual([]);
  });

  it('no file under src/ spells a retired colour', () => {
    // The colour a token replaced is gone from every file, app.css included.
    const hits: string[] = [];
    for (const file of sourceFiles(SRC)) {
      readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
        for (const [name, hex] of Object.entries(RETIRED)) {
          if (spellings(hex).some(re => re.test(line))) {
            hits.push(`${relative(SRC, file)}:${i + 1} spells ${name}: ${line.trim()}`);
          }
        }
      });
    }
    expect(hits).toEqual([]);
  });

  it('the scan catches a hex and an rgba spelling', () => {
    // The scan itself, on two lines a component could hold.
    const [hexRe, rgbRe] = spellings(PROTECTED['--status-failed']);
    // Split so this file does not spell the colour itself.
    const HASH = '#';
    expect(hexRe.test(`  color: ${HASH}f97163;`)).toBe(true);
    expect(rgbRe.test(`  background: rgba(${'249, 113'}, 99, 0.08);`)).toBe(true);
    expect(hexRe.test(`  color: ${HASH}f9716366;`)).toBe(true);
    expect(hexRe.test(`  color: ${HASH}f97163a;`)).toBe(false);
  });
});
