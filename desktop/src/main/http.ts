// Запросы главного процесса к серверу организации — в её собственной сессии Electron (cookie, прокси и сертификаты этой организации), без заголовка Origin.
// Так сервер отличает приложение от страницы (см. backend/app/api/desktop.py), а cookie сессии, выданная при возобновлении, сразу попадает в нужный раздел.
import { net, type Session } from "electron";

export type Resp = { status: number; json: unknown; headers: Record<string, string> };

export function request(ses: Session, method: "GET" | "POST", url: string, body?: unknown, headers: Record<string, string> = {}, timeoutMs = 15000): Promise<Resp> {
  return new Promise((resolve, reject) => {
    const req = net.request({ method, url, session: ses, useSessionCookies: true, redirect: "error", cache: "no-store" });
    req.setHeader("Accept", "application/json");
    req.setHeader("X-Peregovorka-Desktop", "1");
    req.setHeader("User-Agent", ses.getUserAgent());            // подпись устройства на сервере («Peregovorka для Windows …»), как у страниц этой организации
    for (const [k, v] of Object.entries(headers)) req.setHeader(k, v);
    const timer = setTimeout(() => { req.abort(); reject(new Error("net::ERR_TIMED_OUT")); }, timeoutMs);
    req.on("response", (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c) => { chunks.push(c); if (chunks.reduce((n, x) => n + x.length, 0) > 2_000_000) req.abort(); });
      res.on("end", () => {
        clearTimeout(timer);
        let json: unknown = null;
        try { json = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { json = null; }
        const h: Record<string, string> = {};
        for (const [k, v] of Object.entries(res.headers)) h[k.toLowerCase()] = Array.isArray(v) ? v.join(", ") : String(v);
        resolve({ status: res.statusCode, json, headers: h });
      });
      res.on("error", (e: Error) => { clearTimeout(timer); reject(e); });
    });
    req.on("error", (e) => { clearTimeout(timer); reject(e); });
    if (body !== undefined) {
      req.setHeader("Content-Type", "application/json");
      req.write(JSON.stringify(body));
    }
    req.end();
  });
}
