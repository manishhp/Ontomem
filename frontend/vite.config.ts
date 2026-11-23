import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on :8000; in development Vite proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.ONTOMEM_API ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
})
