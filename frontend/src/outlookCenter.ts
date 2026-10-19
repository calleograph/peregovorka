/** Проверки публичной страницы установки Outlook, выполняемые В БРАУЗЕРЕ клиента: именно клиент — тот, кто должен открыть адрес и принять сертификат. */
export interface ClientCheck { key: string; title: string; ok: boolean | null; message: string }

const PRIVATE = /^(localhost|127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.|\[?::1\]?$|0\.0\.0\.0)/i;

/** Адреса из манифеста (атрибуты DefaultValue, начинающиеся с http): нужны для проверки «все ресурсы доступны, внутренних адресов нет». */
export function manifestUrls(xml: string): string[] {
  const out = new Set<string>();
  for (const m of xml.matchAll(/DefaultValue="(https?:\/\/[^"]+)"/g)) out.add(m[1]);
  return [...out];
}

export const isInternalHost = (host: string) => PRIVATE.test(host) || (!host.includes(".") && !host.startsWith("["));

/** Разбор манифеста: идентификатор, версия и замечания (не XML, не HTTPS, внутренние адреса, чужой идентификатор). */
export function inspectManifest(xml: string, expectedId: string): { id: string; version: string; problems: string[]; urls: string[] } {
  const problems: string[] = [];
  let id = "", version = "";
  if (typeof DOMParser !== "undefined" && new DOMParser().parseFromString(xml, "application/xml").querySelector("parsererror")) problems.push("Файл не является корректным XML.");
  id = xml.match(/<Id>\s*([^<\s]+)\s*<\/Id>/)?.[1] ?? "";
  version = xml.match(/<Version>\s*([^<\s]+)\s*<\/Version>/)?.[1] ?? "";
  if (!id) problems.push("В манифесте нет идентификатора надстройки.");
  else if (expectedId && id !== expectedId) problems.push(`Идентификатор ${id} не совпадает с ожидаемым ${expectedId}.`);
  const urls = manifestUrls(xml);
  const insecure = urls.filter((u) => !u.startsWith("https://") && !/^http:\/\/(schemas|www\.w3)/.test(u));
  if (insecure.length) problems.push(`Адреса не по HTTPS: ${insecure.slice(0, 3).join(", ")}.`);
  const internal = urls.filter((u) => { try { return isInternalHost(new URL(u).hostname); } catch { return true; } });
  if (internal.length) problems.push(`Внутренние адреса в манифесте: ${internal.slice(0, 3).join(", ")}.`);
  return { id, version, problems, urls };
}

/** Выполняет проверки с этого компьютера. fetchImpl подменяется в тестах. */
export async function runClientChecks(origin: string, addinId: string, fetchImpl: typeof fetch = fetch): Promise<ClientCheck[]> {
  const out: ClientCheck[] = [];
  const add = (key: string, title: string, ok: boolean | null, message: string) => out.push({ key, title, ok, message });
  add("https", "Защищённое соединение", origin.startsWith("https://"), origin.startsWith("https://") ? "Страница открыта по HTTPS, браузер принял сертификат." : "Страница открыта без HTTPS: Outlook не загрузит надстройку.");
  try {
    const r = await fetchImpl(`${origin}/api/v1/outlook/config`, { cache: "no-store" });
    add("api", "API надстройки", r.ok, r.ok ? "API отвечает с этого компьютера." : `HTTP ${r.status}: ${r.status === 404 ? "маршрут не найден или интеграция выключена" : r.status >= 500 ? "ошибка прокси или сервиса" : "доступ ограничен"}.`);
  } catch { add("api", "API надстройки", false, "Адрес не открылся (сертификат не принят, адрес недоступен или блокируется)."); }
  let xml = "";
  try {
    const r = await fetchImpl(`${origin}/api/v1/outlook/manifest.xml`, { cache: "no-store", redirect: "manual" });
    if (r.type === "opaqueredirect" || (r.status >= 300 && r.status < 400)) add("manifest", "Манифест", false, "Манифест отдаётся через перенаправление: Exchange 2019 его не загрузит. Нужна прямая ссылка.");
    else if (!r.ok) add("manifest", "Манифест", false, `HTTP ${r.status}.`);
    else {
      xml = await r.text();
      const ct = r.headers.get("content-type") ?? "";
      add("manifest", "Манифест", /xml/i.test(ct), /xml/i.test(ct) ? "Отдаётся напрямую по HTTPS, Content-Type XML." : `Неверный Content-Type: ${ct || "нет"}.`);
    }
  } catch { add("manifest", "Манифест", false, "Манифест не загрузился."); }
  if (xml) {
    const m = inspectManifest(xml, addinId);
    add("manifest_content", "Содержимое манифеста", m.problems.length === 0, m.problems.length ? m.problems.join(" ") : `Идентификатор ${m.id} постоянен, версия ${m.version}, внутренних адресов и не-HTTPS адресов нет.`);
    const own = m.urls.filter((u) => u.startsWith(origin + "/"));
    const bad: string[] = [];
    for (const u of own) {
      try { const r = await fetchImpl(u, { cache: "no-store" }); if (!r.ok) bad.push(`${u.slice(origin.length)}: HTTP ${r.status}`); } catch { bad.push(`${u.slice(origin.length)}: не открылся`); }
    }
    if (!own.length) add("resources", "Ресурсы надстройки", null, "Манифест ссылается на другой адрес, а не на тот, по которому открыта эта страница: откройте страницу по публичному адресу, указанному в настройках Outlook.");
    else add("resources", "Ресурсы надстройки", bad.length === 0, bad.length ? `Недоступны: ${bad.join("; ")}.` : `Все ресурсы на этом сервере доступны (${own.length}).`);
  }
  return out;
}
