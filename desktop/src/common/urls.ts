// Адреса серверов и ссылок: единственное место, где решается, что считается «тем же сервером», «разрешённым доменом» и «безопасной внешней ссылкой».

export type UrlError = "empty" | "invalid" | "not_https" | "credentials" | "path";

/** Адрес сервера, введённый человеком («meet.example.org», «https://meet.example.org/»), → origin `https://host[:port]`. Только HTTPS:
 *  проверка TLS не отключается ни для каких серверов. `allowHttpLoopback` — только для тестового стенда на 127.0.0.1 (флаг командной строки разработчика). */
export function normalizeServerUrl(input: string, opts: { allowHttpLoopback?: boolean } = {}): { origin: string } | { error: UrlError } {
  const raw = (input || "").trim();
  if (!raw) return { error: "empty" };
  if (raw.length > 400) return { error: "invalid" };
  let u: URL;
  try {
    u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    return { error: "invalid" };
  }
  if (u.username || u.password) return { error: "credentials" };
  const loopback = u.hostname === "127.0.0.1" || u.hostname === "localhost" || u.hostname === "[::1]";
  if (u.protocol !== "https:" && !(u.protocol === "http:" && loopback && opts.allowHttpLoopback)) return { error: "not_https" };
  if (!u.hostname || /[\s<>"'`]/.test(u.hostname)) return { error: "invalid" };
  if (u.pathname !== "/" && u.pathname !== "") return { error: "path" };      // сервер Peregovorka всегда в корне домена
  return { origin: u.origin };
}

export function originOf(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.origin : null;
  } catch {
    return null;
  }
}

export const sameOrigin = (url: string, origin: string): boolean => originOf(url) === origin;

/** Политика «разрешённые домены»: точное имя или `*.example.org` (поддомены, но не сам домен). Пустой список — ограничения нет. */
export function domainAllowed(host: string, patterns: string[] | undefined): boolean {
  if (!patterns || patterns.length === 0) return true;
  const h = host.toLowerCase().replace(/\.$/, "");
  return patterns.some((p) => {
    const q = p.trim().toLowerCase().replace(/\.$/, "");
    if (!q) return false;
    if (q.startsWith("*.")) return h.endsWith(q.slice(1)) && h.length > q.length - 1;
    return h === q;
  });
}

/** Путь внутри сервера, на который можно перейти по команде (уведомление, ссылка приложения): только относительный путь без «//», схемы и управляющих символов. */
export function safeRoute(route: unknown): string | null {
  if (typeof route !== "string" || route.length > 512) return null;
  if (!route.startsWith("/") || route.startsWith("//") || route.includes("\\")) return null;
  if (/[\u0000-\u001f\u007f]/.test(route)) return null;
  try {
    const u = new URL(route, "https://placeholder.invalid");
    if (u.origin !== "https://placeholder.invalid") return null;
    return u.pathname + u.search + u.hash;
  } catch {
    return null;
  }
}

/** Ссылка, которую можно отдать системному браузеру или почтовой программе. Остальные схемы (file:, javascript:, ms-*, сетевые пути) не открываются. */
export function externalAllowed(url: string): boolean {
  try {
    const u = new URL(url);
    if (u.protocol === "mailto:" || u.protocol === "tel:") return url.length < 2048;
    return (u.protocol === "https:" || u.protocol === "http:") && !u.username && !u.password && url.length < 4096;
  } catch {
    return false;
  }
}
