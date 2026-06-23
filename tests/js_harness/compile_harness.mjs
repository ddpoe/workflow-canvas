/**
 * Node harness that imports compilePipelineToJSON from the canvas's pure
 * compile module, runs it on a fixture read from stdin, and prints the
 * resulting JSON to stdout.
 *
 * Invoked by tests/test_canvas_compile_ts.py via:
 *   node --experimental-strip-types compile_harness.mjs
 *
 * The harness deliberately imports from compile.ts (not pipeline.ts) to
 * avoid pulling in svelte/store runtime deps that would require node_modules.
 * compile.ts only has type-only imports, so --experimental-strip-types
 * erases every non-JS symbol and the file runs under plain Node.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Resolve to the canvas compile module.  Two levels up from tests/js_harness.
const compilePath = resolve(
  __dirname,
  '..',
  '..',
  'wfc',
  'canvas',
  'static',
  'src',
  'lib',
  'compile.ts',
);

// Windows requires a file:// URL for dynamic import of absolute paths.
const { compilePipelineToJSON } = await import(pathToFileURL(compilePath).href);

// Read authoring-state fixture from stdin.
const stdinData = readFileSync(0, 'utf8');
const state = JSON.parse(stdinData);

const compiled = compilePipelineToJSON(state);
process.stdout.write(JSON.stringify(compiled));
