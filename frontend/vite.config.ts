import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке прокси на backend/livekit; значения — только локальные, не относятся к развёртыванию.
const backend = process.env.DEV_BACKEND_URL ?? "http://127.0.0.1:8000";
// Редактор схем draw.io в контейнере web лежит в /drawio/ (frontend/Dockerfile); в разработке его можно отдать любым статическим сервером.
const drawio = process.env.DEV_DRAWIO_URL ?? "http://127.0.0.1:8771";
// HLS прямых эфиров в контейнере web раздаёт nginx (/hls/…); в разработке — любой nginx с тем же конфигом (tests/live/egress/run_live.py).
const hls = process.env.DEV_HLS_URL ?? "http://127.0.0.1:18088";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Как контейнер web (nginx.conf): backend получает адрес подключившегося в X-PG-Peer. Для проверок защитного слоя адрес можно подменить заголовком X-Dev-Peer (только dev-прокси).
      "/api": { target: backend, changeOrigin: false, ws: true, configure: (proxy) => {
        const peer = (req: import("http").IncomingMessage) => String(req.headers["x-dev-peer"] || req.socket.remoteAddress || "").replace(/^::ffff:/, "");
        proxy.on("proxyReq", (p, req) => { p.removeHeader("x-dev-peer"); p.setHeader("X-PG-Peer", peer(req)); });
        proxy.on("proxyReqWs", (p, req) => { p.removeHeader("x-dev-peer"); p.setHeader("X-PG-Peer", peer(req)); });
      } },
      "/drawio": { target: drawio, changeOrigin: false },
      "/hls": { target: hls, changeOrigin: false },
    },
  },
  // livekit-client даёт крупный чанк страницы комнаты: это не ошибка (грузится лениво), порог предупреждения поднят.
  build: { sourcemap: false, target: "es2022", chunkSizeWarningLimit: 900 },
  test: { environment: "node" },
});
