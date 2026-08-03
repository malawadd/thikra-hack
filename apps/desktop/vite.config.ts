import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  clearScreen: false,
  server: { strictPort: true },
  plugins: [sveltekit()],
  test: { environment: 'node', include: ['src/**/*.test.ts'] }
});
