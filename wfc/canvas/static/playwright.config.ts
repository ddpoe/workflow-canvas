import { defineConfig, devices } from '@playwright/test';

/**
 * ADR-015 Phase D Layer 3 (entry point): Playwright config.
 *
 * Single chromium project, Vite dev server.  No FastAPI process — all
 * backend routes are intercepted in-test via `setupRouteReplay`.
 *
 * Port choice: Vite default 5174 is fine; we pin via the dev script.
 * The user's machine has port 8000 squatted by uniFLOW (302->8443),
 * so this config never touches 8000.
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  retries: 0,
  reporter: 'list',
  // Sequential execution. Parallel workers all hit the same Vite dev
  // server, and the resulting contention slips the running-paint window
  // intermittently (~30% flake observed at workers=auto). Wall-clock
  // for the suite is ~30-40s sequentially — well under US-8's 60s.
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
