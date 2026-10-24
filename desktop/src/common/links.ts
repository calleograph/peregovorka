// Ссылки приложения `peregovorka://open?url=<обычная ссылка на встречу>`. Ссылка не несёт команд: из неё берутся только сервер и путь встречи,
// а путь проверяется по белому списку разделов. Всё остальное отклоняется.
import { originOf } from "./urls";

export const SCHEME = "peregovorka";

/** Разделы, которые можно открыть по ссылке: комната, встреча из календаря, личная ссылка, гостевой вход, материалы встречи, главная и история. */
const ROUTES = [/^\/$/, /^\/history$/, /^\/rooms\/[A-Za-z0-9._~-]{1,120}$/, /^\/m\/[A-Za-z0-9_-]{4,120}$/, /^\/p\/[A-Za-z0-9_-]{4,120}$/,
  /^\/guest\/[A-Za-z0-9_-]{8,200}$/, /^\/history\/[0-9a-fA-F-]{8,64}$/];

export type DeepLink = { origin: string; route: string };

export function routeAllowed(path: string): boolean {
  return ROUTES.some((r) => r.test(path));
}

/** Разбор ссылки приложения. Возвращает null для всего, что не точно `peregovorka://open?url=https://…/<разрешённый путь>`. */
export function parseDeepLink(raw: unknown, opts: { allowHttpLoopback?: boolean } = {}): DeepLink | null {
  if (typeof raw !== "string" || raw.length > 2048) return null;
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return null;
  }
  if (u.protocol !== `${SCHEME}:` || u.hostname !== "open" || u.username || u.password || u.port) return null;
  if (u.pathname !== "" && u.pathname !== "/") return null;
  const keys = [...u.searchParams.keys()];
  if (keys.length !== 1 || keys[0] !== "url") return null;
  return parseMeetingUrl(u.searchParams.get("url") || "", opts);
}

/** Обычная ссылка на встречу (из письма или браузера) → сервер и путь. Используется и для `peregovorka://`, и для ссылок, вставленных в мастер. */
export function parseMeetingUrl(raw: string, opts: { allowHttpLoopback?: boolean } = {}): DeepLink | null {
  if (raw.length > 1500) return null;
  let t: URL;
  try {
    t = new URL(raw);
  } catch {
    return null;
  }
  const loopbackHttp = opts.allowHttpLoopback && t.protocol === "http:" && (t.hostname === "127.0.0.1" || t.hostname === "localhost");     // только стенд разработчика
  if ((t.protocol !== "https:" && !loopbackHttp) || t.username || t.password) return null;
  const origin = originOf(t.href);
  if (!origin) return null;
  const path = t.pathname.replace(/\/+$/, "") || "/";
  if (!routeAllowed(path)) return null;
  return { origin, route: path };
}

/** Аргумент командной строки, похожий на ссылку приложения (Windows передаёт её отдельным аргументом при запуске и второму экземпляру). */
export function findDeepLinkArg(argv: string[]): string | null {
  return argv.find((a) => typeof a === "string" && a.toLowerCase().startsWith(`${SCHEME}://`)) ?? null;
}
