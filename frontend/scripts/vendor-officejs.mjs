// Локальная поставка Office.js для надстройки Outlook: при сборке образа web файлы берутся с официального CDN Microsoft и кладутся в dist/outlook/assets/.
// Зачем: страницы надстройки идут под строгой CSP (без unsafe-eval). Прежняя сборка office.js (appsforoffice.microsoft.com) в Outlook Web App требует MicrosoftAjax.js,
// а тот разбирает JSON через eval: под CSP он падает, и Outlook показывает «Ошибка настройки». Сборка с officeapis.public.onecdn.static.microsoft помечена
// `osfLoadMsAjax = false` и MicrosoftAjax не требует; собственная копия к тому же снимает зависимость запуска панели от доступа клиентов к CDN.
//
// Запуск: node scripts/vendor-officejs.mjs [каталог dist]. Состав файлов выводится из самого office.js (языки, версии файлов узла Outlook), а не из жёсткого списка,
// поэтому новая сборка Microsoft забирается целиком. Скрипт НИКОГДА не роняет сборку: при любом сбое (нет сети, CDN недоступен, ответ не похож на скрипт, сборка
// требует MicrosoftAjax) каталог assets не создаётся, печатается предупреждение, а страницы надстройки (ms-loader.js) сами берут Office.js с CDN.
// Файлы Microsoft не изменяются (лицензия запрещает); лицензия кладётся рядом.
import { createHash } from "node:crypto";
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** Единственный источник: только сборка с этого адреса не требует MicrosoftAjax (на appsforoffice.microsoft.com — прежняя сборка, которая требует). */
export const SOURCES = ["https://officeapis.public.onecdn.static.microsoft/1/"];
const LICENSE_URL = "https://raw.githubusercontent.com/OfficeDev/office-js/release/LICENSE.md";
// Файлы узла Outlook старых форматов (O15: Exchange 2013 и надстройки без сведений о узле) — не перечислены в office.js; нет файла — не ошибка.
const O15_FILES = ["o15apptofilemappingtable.js", "outlook-15.js", "outlook-15.01.js", "outlook-15.02.js", "outlook-15.03.js", "outlook-15.04.js", "outlookwebapp-15.js", "outlookwebapp-15.01.js"];
const EXTRA_FILES = ["es6-promise.js", "ariatelemetry/aria-web-telemetry.js", "telemetry/oteljs_agave.js", "telemetry/oteljs.js"];
const PLATFORMS = ["web", "win32", "mac", "ios", "android", "universal"];

/** Из текста office.js: языки и файлы узла Outlook. Возвращает { required, optional } — пути относительно каталога office.js. */
export function plan(officeJs) {
  const locales = [...(/OSF\.SupportedLocales=\{([^}]*)\}/.exec(officeJs)?.[1] ?? "").matchAll(/"([a-z]{2,3}(?:-[a-z0-9]+)+)"\s*:/g)].map((m) => m[1]);
  const map = /OSF\.HostSpecificFileVersionMap=\{.*?outlook:\{([^}]*)\}/.exec(officeJs)?.[1] ?? "";
  const def = /OSF\.HostSpecificFileVersionDefault="([\d.]+)"/.exec(officeJs)?.[1] ?? "16.00";
  const host = [...map.matchAll(/([a-z0-9]+):"([\d.]+)"/g)].map((m) => `outlook-${m[1]}-${m[2]}.js`);
  const required = [...new Set([
    ...host,
    ...(locales.length ? locales : ["en-us", "ru-ru"]).flatMap((l) => [`${l}/office_strings.js`, `${l}/outlook_strings.js`]),
  ])];
  const optional = [...new Set([
    ...PLATFORMS.map((p) => `outlook-${p}-${def}.js`),
    ...O15_FILES, ...EXTRA_FILES,
  ])].filter((f) => !required.includes(f));
  return { required, optional, locales, host };
}

/** Сборка office.js не требует MicrosoftAjax (флаг, который Microsoft ставит в начале файла). */
export const noMsAjax = (officeJs) => /osfLoadMsAjax\s*=\s*false/.test(officeJs.slice(0, 20000));

/** Ответ похож на скрипт Microsoft, а не на страницу прокси/ошибки. */
export function looksLikeScript(name, text) {
  if (text.length < 200) return false;
  if (/^\s*<(!doctype|html|head|body)/i.test(text)) return false;
  return name !== "office.js" || /Office JavaScript API library|OSF\.|Microsoft\.Office/.test(text);
}

async function get(url, tries = 3) {
  let last = "";
  for (let i = 1; i <= tries; i++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(30000), headers: { "User-Agent": "peregovorka-build/1" } });
      if (r.status === 404) return { status: 404, buf: null };
      if (r.ok) return { status: 200, buf: Buffer.from(await r.arrayBuffer()) };
      last = `HTTP ${r.status}`;
    } catch (e) { last = String(e?.cause?.code || e?.message || e); }
    if (i < tries) await new Promise((res) => setTimeout(res, 1500 * i));
  }
  throw new Error(`${url}: ${last}`);
}

async function pool(items, n, fn) {
  const out = []; let i = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; out[k] = await fn(items[k]); } }));
  return out;
}

async function fetchSet(base, outDir, log) {
  const office = await get(base + "office.js");
  const officeText = office.buf?.toString("utf8") ?? "";
  if (!office.buf || !looksLikeScript("office.js", officeText)) throw new Error(`${base}office.js: не похоже на библиотеку Office.js`);
  if (!noMsAjax(officeText)) throw new Error(`${base}office.js: сборка требует MicrosoftAjax.js, который не работает под CSP страниц надстройки`);
  const files = new Map([["office.js", office.buf]]);
  const { required, optional, locales, host } = plan(officeText);
  if (!host.length) throw new Error("в office.js не найден список файлов узла Outlook (формат библиотеки изменился)");
  const miss = [];
  await pool([...required.map((f) => [f, true]), ...optional.map((f) => [f, false])], 8, async ([f, must]) => {
    const r = await get(base + f);
    if (r.buf && looksLikeScript(f, r.buf.toString("utf8"))) files.set(f, r.buf);
    else if (must) miss.push(f);
  });
  if (miss.length) throw new Error(`не получены обязательные файлы (${miss.length}): ${miss.slice(0, 5).join(", ")}`);
  for (const [name, buf] of files) { const p = join(outDir, "office-js", name); await mkdir(dirname(p), { recursive: true }); await writeFile(p, buf); }
  let license = "";
  try { license = (await get(LICENSE_URL, 2)).buf?.toString("utf8") ?? ""; } catch { /* лицензия нужна рядом с файлами, но не должна ронять сборку */ }
  await writeFile(join(outDir, "LICENSE-office-js.md"), license || "Лицензия: https://github.com/OfficeDev/office-js/blob/release/LICENSE.md\n");
  const sha = (b) => createHash("sha256").update(b).digest("hex");
  const info = { source: base, built_at: new Date().toISOString(), files: files.size, locales: locales.length, office_js_sha256: sha(office.buf) };
  await writeFile(join(outDir, "SOURCE.json"), JSON.stringify(info, null, 1) + "\n");
  log(`Office.js: ${files.size} файлов с ${base}`);
  return info;
}

export async function vendor(dist, log = console.log, sources = SOURCES) {
  const final = join(dist, "outlook", "assets"), tmp = join(dist, "outlook", ".assets-tmp");
  await rm(tmp, { recursive: true, force: true });
  const errors = [];
  for (const base of sources) {
    try {
      await mkdir(tmp, { recursive: true });
      const info = await fetchSet(base, tmp, log);
      await rm(final, { recursive: true, force: true });
      await rename(tmp, final);
      return info;
    } catch (e) {
      errors.push(String(e.message || e));
      await rm(tmp, { recursive: true, force: true });
    }
  }
  await rm(final, { recursive: true, force: true });
  log(`ПРЕДУПРЕЖДЕНИЕ: локальная копия Office.js не создана (${errors.join("; ")}). Образ собран, панель Outlook будет брать библиотеку с CDN Microsoft; ` +
      "повторите сборку с доступом к officeapis.public.onecdn.static.microsoft.");
  return null;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const dist = process.argv[2] || join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
  vendor(dist).then(() => process.exit(0), (e) => { console.log("ПРЕДУПРЕЖДЕНИЕ: " + (e?.message || e)); process.exit(0); });
}
