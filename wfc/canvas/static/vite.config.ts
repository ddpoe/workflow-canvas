import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import basicSsl from '@vitejs/plugin-basic-ssl';

// Fallback matches the canvas server's default port. `dev.py --with-vite`
// exports WFC_CANVAS_API_URL so the proxy follows whatever --port the
// backend was launched with.
const apiTarget = process.env.WFC_CANVAS_API_URL ?? 'http://localhost:8500';

export default defineConfig({
  plugins: [svelte(), basicSsl()],
  server: {
    https: {},
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
