import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    // 0.0.0.0 INSIDE the container so Docker networking works. The host port is
    // published to 127.0.0.1 only, by docker-compose.yml.
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
  },
});
