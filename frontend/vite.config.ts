import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";

// In dev the UI talks to the API through this proxy, so the browser only ever
// sees one origin and cookies/CORS behave the same as in production.
export default defineConfig({
  plugins: [react(), tailwind()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: process.env.API_URL ?? "http://localhost:8080", changeOrigin: true },
      "/api": { target: process.env.API_URL ?? "http://localhost:8080", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
