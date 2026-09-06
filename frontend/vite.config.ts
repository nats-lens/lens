import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// In dev the SPA is served here and the API lives in another container, so both
// /api and /ws are proxied. In production neither proxy exists: one process
// serves the built assets and the API from the same origin.
const target = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: true },
      "/ws": { target, ws: true, changeOrigin: true },
      // Anchored: a bare "/schema" prefix also swallows the app's own /schemas
      // screen and hands it to the backend, which 404s it.
      "^/schema(/.*)?$": { target, changeOrigin: true },
    },
  },
});
