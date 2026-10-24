import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': { target: 'https://localhost:8443', changeOrigin: true, secure: false },
      '/ws': { target: 'https://localhost:8443', ws: true, changeOrigin: true, secure: false },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
});
