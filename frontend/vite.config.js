import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built UI is served same-origin by the Mucaro backend. In `npm run dev`, API paths are
// proxied to the local backend so the browser never needs cross-origin access.
const apiPaths = [
  "/health", "/config", "/security", "/models", "/offline-manifest", "/conversations", "/work-runs", "/interactions",
  "/tools", "/sources", "/chunks", "/ingest", "/ingest-path", "/permissions", "/memory", "/runs", "/exports",
  "/workspaces", "/validate", "/plan", "/approve", "/ask", "/agent", "/artifacts", "/coding",
  "/goals", "/intelligence-plane", "/ocr", "/skills", "/tasks", "/web-captures",
];

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: Object.fromEntries(apiPaths.map((path) => [path, { target: process.env.MUCARO_AI_DEV_API_TARGET || "http://127.0.0.1:8000", changeOrigin: false }])),
  },
  build: { sourcemap: false, target: "safari15" },
});
