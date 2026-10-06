import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке прокси на backend/livekit; значения — только локальные, не относятся к развёртыванию.
const backend = process.env.DEV_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: false, ws: true },
    },
  },
  build: { sourcemap: false, target: "es2022" },
  test: { environment: "node" },
});
