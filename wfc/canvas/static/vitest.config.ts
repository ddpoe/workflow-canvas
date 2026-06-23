import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';

export default defineConfig({
  // svelteTesting() switches the svelte plugin to the browser/client
  // build under jsdom so @testing-library/svelte's `render(...)` can
  // mount Svelte 5 components (the SSR build throws
  // `mount(...) is not available on the server`).
  plugins: [svelte({ hot: !process.env.VITEST }), svelteTesting()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test-setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,js}'],
  },
});
