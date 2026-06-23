#!/usr/bin/env node
/**
 * ADR-015 Phase D Layer 1: OpenAPI -> TS codegen entrypoint.
 *
 * Reads the offline OpenAPI snapshot produced by `dump-openapi.py`
 * and runs `openapi-typescript` against it, writing the generated
 * TypeScript types to `src/lib/types/api.ts`.
 *
 * Why offline-first: the frontend codegen runs in CI / `prebuild`
 * without spinning up FastAPI. The snapshot is committed to the repo
 * (Architect decision D-3) so contract drift surfaces as a PR diff.
 *
 * Refresh flow:
 *   1. Edit a Pydantic model in wfc/canvas/server.py
 *   2. `poetry run python wfc/canvas/static/scripts/dump-openapi.py`
 *   3. `npm run codegen`
 *   4. Commit both the snapshot and the regenerated types together.
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, '..');
const snapshotPath = path.join(__dirname, 'openapi.snapshot.json');
const outPath = path.join(projectRoot, 'src/lib/types/api.ts');

if (!existsSync(snapshotPath)) {
  console.error(`[codegen] snapshot not found at ${snapshotPath}`);
  console.error(`[codegen] regenerate via: poetry run python wfc/canvas/static/scripts/dump-openapi.py`);
  process.exit(1);
}

const result = spawnSync(
  'npx',
  ['openapi-typescript', snapshotPath, '-o', outPath],
  { stdio: 'inherit', shell: true },
);

if (result.status !== 0) {
  console.error('[codegen] openapi-typescript failed');
  process.exit(result.status ?? 1);
}

console.log(`[codegen] wrote ${outPath}`);
