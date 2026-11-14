import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard is a pure client of the AI Factory API. In dev, Vite proxies
// /api to the local API; in the container, nginx does the same (see nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8081", changeOrigin: true },
      "/healthz": { target: "http://127.0.0.1:8081", changeOrigin: true },
      "/readyz": { target: "http://127.0.0.1:8081", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
