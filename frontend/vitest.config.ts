import { defineConfig } from 'vitest/config';
import path from 'node:path';

/**
 * Unit cover for the parts where a silent wrong answer is plausible: graph layout, blast-radius
 * construction, replay folding and the derivation adapters. Rendering is verified by the surface
 * harness in scripts/, which drives a real browser.
 */
export default defineConfig({
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    reporters: 'basic',
  },
});
