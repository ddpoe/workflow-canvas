import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config.
 *
 * Single chromium project, Vite dev server.  No FastAPI process — all
 * backend routes are intercepted in-test via `setupRouteReplay`.
 *
 * Port choice: Vite default 5174 is fine; we pin via the dev script.
 * The user's machine has port 8000 squatted by uniFLOW (302->8443),
 * so this config never touches 8000.
 */

/**
 * Gallery capture is OPT-IN, and off by default.
 *
 * `behaviors.spec.ts` and `param-editor-gallery.spec.ts` both drop a PNG
 * (and, for four rows, a .webm) under `gallery/states/`.  Those files are
 * tracked in git on purpose — the docs link them — so every incidental
 * `npx playwright test` used to rewrite 24 tracked files and dirty the
 * working tree.
 *
 * What is gated is the *file write*, never a test.  Every assertion in both
 * specs runs on every run, so a default run keeps the full 36-test suite;
 * only `gallery/` stops being touched.  This is the analogue of pytest's
 * `addopts = -m "not slow and not integration"`: excluded at the config
 * level, opted into explicitly.
 *
 * Two equivalent opt-ins:
 *
 *   - `PW_GALLERY=1 npx playwright test`
 *     (PowerShell: `$env:PW_GALLERY=1; npx playwright test`)
 *   - `npm run test:e2e:gallery`
 *
 * The two npm scripts are textually identical on purpose — **the script name
 * IS the flag.**  npm exports the running script's name as
 * `npm_lifecycle_event`, which works in every shell; an inline `VAR=1 cmd`
 * in a package.json script does not, because npm's default shell on Windows
 * is cmd.exe.  Do not "simplify" the two scripts into one.
 */
export const CAPTURE_GALLERY =
  process.env.PW_GALLERY === '1' ||
  process.env.npm_lifecycle_event === 'test:e2e:gallery';
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  retries: 0,
  reporter: 'list',
  // Sequential execution. Parallel workers all hit the same Vite dev
  // server, and the resulting contention slips the running-paint window
  // intermittently (~30% flake observed at workers=auto). Wall-clock
  // for the suite is ~30-40s sequentially — well under a 60s budget.
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: 'https://localhost:5174',
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'npm run dev -- --port 5174 --strictPort',
    url: 'https://localhost:5174',
    ignoreHTTPSErrors: true,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
