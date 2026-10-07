import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке прокси на backend/livekit; значения — только локальные, не относятся к развёртыванию.
const backend = process.env.DEV_BACKEND_URL ?? "http://127.0.0.1:8000";
// Редактор схем draw.io в контейнере web лежит в /drawio/ (frontend/Dockerfile); в разработке его можно отдать любым статическим сервером.
const drawio = process.env.DEV_DRAWIO_URL ?? "http://127.0.0.1:8771";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: false, ws: true },
      "/drawio": { target: drawio, changeOrigin: false },
    },
  },
  // livekit-client даёт крупный чанк страницы комнаты: это не ошибка (грузится лениво), порог предупреждения поднят.
  build: { sourcemap: false, target: "es2022", chunkSizeWarningLimit: 900 },
  test: { environment: "node" },
});
