/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev only: proxy API paths to the running FastAPI backend so the SPA is same-origin
// in development. Production same-origin serving is Stage 4 (multi-stage Docker build).
const API_PATHS = ['/chat', '/conversations', '/history', '/works', '/recommendations', '/analysis', '/books', '/health']

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      API_PATHS.map((p) => [p, { target: 'http://localhost:8080', changeOrigin: true }]),
    ),
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
  },
})
