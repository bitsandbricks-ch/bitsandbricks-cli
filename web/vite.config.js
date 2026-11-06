import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The build lands in the Python package, which serves it (src/mortar/server/static).
// `npm run dev` proxies /api to an operator on :8080 (MORTAR_DEV=1 mortar serve).
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '../src/mortar/server/static',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
    assetsInlineLimit: 0, // the CSP allows fonts and images from 'self' only, not data: URIs
  },
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8080' },
  },
})
