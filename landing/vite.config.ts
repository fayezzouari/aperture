import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The screenshots live in docs/ so the README and the landing page share one
// copy. Vite serves publicDir at the site root, so they resolve as
// /screenshots/<page>.png in dev and in the build.
//
// Two pages: the landing page at / and the user guide at /docs/.
export default defineConfig({
  plugins: [react()],
  publicDir: "../docs",
  server: { port: 5174 },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        docs: resolve(__dirname, "docs/index.html"),
      },
    },
  },
});
