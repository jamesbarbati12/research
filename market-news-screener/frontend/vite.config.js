import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    // Local dev/build stays at root ("/"). The GitHub Pages deployment sets
    // VITE_BASE_PATH to the nested path it's actually served from (e.g.
    // "/research/screener/"), since Vite emits absolute asset URLs by
    // default and a static site served from a subpath needs those prefixed
    // or every asset request 404s.
    base: env.VITE_BASE_PATH || "/",
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
  };
});
