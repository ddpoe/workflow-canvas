// Copy the repository's CHANGELOG.md into the built canvas (dist/), so an
// installed wfc carries its own changelog (wfc.release reads it there).
//
// Runs as `postbuild`: Vite empties dist/ when it builds, so a copy made
// before the build would be wiped. Fails the build when the changelog is
// missing.
//
// Usage: node scripts/copy-changelog.mjs [<changelog> [<dist dir>]]
// (the defaults are the repository's CHANGELOG.md and this canvas's dist/).

import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(process.argv[2] ?? join(here, '..', '..', '..', '..', 'CHANGELOG.md'));
const distDir = resolve(process.argv[3] ?? join(here, '..', 'dist'));

if (!existsSync(source)) {
  console.error(`copy-changelog: ${source} is missing; the built canvas must carry the changelog.`);
  process.exit(1);
}
mkdirSync(distDir, { recursive: true });
copyFileSync(source, join(distDir, 'CHANGELOG.md'));
console.log(`copy-changelog: copied ${source} to ${join(distDir, 'CHANGELOG.md')}`);
