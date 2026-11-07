import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Read .env from the repo root, not web/.
  envDir: '..',
  // three.js makes the bundle ~2 MB. Fine for a local demo; don't spend hackathon time on it.
  build: { chunkSizeWarningLimit: 3000 },
  server: {
    port: 5173,
    // `npm run dev` runs the FastAPI server on :8000; /api (including the /api/live websocket) goes there.
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
})
