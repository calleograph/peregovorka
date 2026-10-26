import { mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SOURCES, looksLikeScript, noMsAjax, plan, validFile, vendor } from "./vendor-officejs.mjs";

const OFFICE = `osfLoadMsAjax = false;\n/* Office JavaScript API library */ var OSF=OSF||{};OSF.HostSpecificFileVersionDefault="16.00";OSF.HostSpecificFileVersionMap={excel:{web:"16.00"},outlook:{android:"16.00",mac:"16.00",web:"16.01",win32:"16.02"},word:{web:"16.00"}};OSF.SupportedLocales={"en-us":true,"ru-ru":true,"sr-latn-rs":true};${"x".repeat(300)}`;
const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(80)]);
const dirs = [];
const tmp = async () => { const d = await mkdtemp(join(tmpdir(), "vend-")); dirs.push(d); return d; };
afterEach(async () => { vi.unstubAllGlobals(); for (const d of dirs.splice(0)) await rm(d, { recursive: true, force: true }); });

/** Подставной CDN: адрес → тело; чего нет в таблице — 404. */
function cdn(files, base) {
  return vi.fn(async (url) => {
    const name = String(url).startsWith(base) ? String(url).slice(base.length) : null;
    if (name !== null && name in files) return new Response(files[name], { status: 200 });
    return new Response("not found", { status: 404 });
  });
}
const FULL = (extra = {}) => ({
  "office.js": OFFICE,
  ...Object.fromEntries(["outlook-android-16.00", "outlook-mac-16.00", "outlook-web-16.01", "outlook-win32-16.02"].map((n) => [n + ".js", `// ${n}\n${"h".repeat(300)}`])),
  "AgaveErrorUX/index.html": '<!DOCTYPE html><html><body><script src="AgaveErrorUX.js"></script>' + " ".repeat(300) + "</body></html>",
  "AgaveErrorUX/AgaveErrorUX.js": `// agave\n${"a".repeat(300)}`, "AgaveErrorUX/style/AgaveErrorUX.css": `html{margin:0}${" ".repeat(300)}`,
  ...Object.fromEntries(["moe_status_icons", "agavedefaulticon96x96"].map((n) => [`AgaveErrorUX/images/${n}.png`, PNG])),
  ...Object.fromEntries(["en-us", "ru-ru", "sr-latn-rs"].flatMap((l) => [`${l}/office_strings.js`, `${l}/outlook_strings.js`].map((f) => [f, `// ${f}\n${"s".repeat(300)}`]))),
  ...extra,
});

describe("vendor-officejs: состав файлов выводится из самого office.js", () => {
  it("языки и файлы узла Outlook берутся из библиотеки, чужие узлы (Excel, Word) не нужны", () => {
    const p = plan(OFFICE);
    expect(p.host).toEqual(["outlook-android-16.00.js", "outlook-mac-16.00.js", "outlook-web-16.01.js", "outlook-win32-16.02.js"]);
    expect(p.locales).toEqual(["en-us", "ru-ru", "sr-latn-rs"]);
    expect(p.required).toContain("sr-latn-rs/outlook_strings.js");
    expect(p.required.some((f) => /excel|word/.test(f))).toBe(false);
    expect(p.optional).toContain("o15apptofilemappingtable.js");           // старые форматы (Exchange 2013) необязательны: нет файла — не ошибка
    expect(p.optional).not.toContain("outlook-web-16.01.js");               // обязательные не дублируются в необязательных
  });

  it("единственный источник — officeapis…: сборка со старого адреса требует MicrosoftAjax и отвергается", async () => {
    expect(SOURCES).toEqual(["https://officeapis.public.onecdn.static.microsoft/1/"]);
    expect(noMsAjax(OFFICE)).toBe(true);
    expect(noMsAjax(OFFICE.replace("osfLoadMsAjax = false;", ""))).toBe(false);
    const dist = await tmp();
    const old = OFFICE.replace("osfLoadMsAjax = false;", "");
    vi.stubGlobal("fetch", cdn(FULL({ "office.js": old }), "https://old.example.org/"));
    const log = vi.fn();
    expect(await vendor(dist, log, ["https://old.example.org/"])).toBeNull();
    expect(log.mock.calls.join("\n")).toMatch(/требует MicrosoftAjax/);
  });

  it("страница подтверждения доверия к узлу Outlook (AgaveErrorUX) входит в обязательный набор с точным регистром имён", () => {
    const p = plan(OFFICE);
    for (const f of ["AgaveErrorUX/index.html", "AgaveErrorUX/AgaveErrorUX.js", "AgaveErrorUX/style/AgaveErrorUX.css", "AgaveErrorUX/images/moe_status_icons.png"]) expect(p.required).toContain(f);
    expect(validFile("AgaveErrorUX/index.html", Buffer.from("<html>proxy error</html>" + " ".repeat(300)))).toBe(false);
    expect(validFile("AgaveErrorUX/images/office.png", Buffer.from("<html>not a png</html>" + " ".repeat(100)))).toBe(false);
    expect(validFile("AgaveErrorUX/images/office.png", PNG)).toBe(true);
  });

  it("ответ прокси или страница ошибки вместо скрипта не принимается", () => {
    expect(looksLikeScript("a.js", "<!DOCTYPE html><html>proxy</html>" + " ".repeat(300))).toBe(false);
    expect(looksLikeScript("a.js", "short")).toBe(false);
    expect(looksLikeScript("office.js", "var a=1;" + "z".repeat(300))).toBe(false);           // office.js должен быть похож на библиотеку Office
    expect(looksLikeScript("office.js", OFFICE)).toBe(true);
  });
});

describe("vendor-officejs: поставка в dist", () => {
  it("успех: файлы Office.js, лицензия и SOURCE.json; MicrosoftAjax.js не поставляется", async () => {
    const dist = await tmp();
    vi.stubGlobal("fetch", cdn(FULL(), "https://cdn-a.example.org/1/"));
    const info = await vendor(dist, () => undefined, ["https://cdn-a.example.org/1/"]);
    const a = join(dist, "outlook", "assets");
    expect(info).toMatchObject({ source: "https://cdn-a.example.org/1/", locales: 3 });
    expect(await readFile(join(a, "office-js", "office.js"), "utf8")).toBe(OFFICE);
    expect((await readdir(a)).sort()).toEqual(["LICENSE-office-js.md", "SOURCE.json", "office-js"]);        // SOURCE.json в корне assets: его читает проверка в администрировании
    expect((await readdir(join(a, "office-js"))).some((f) => /ajax/i.test(f))).toBe(false);
    expect((await stat(join(a, "office-js", "ru-ru", "outlook_strings.js"))).size).toBeGreaterThan(100);
    expect((await readFile(join(a, "office-js", "AgaveErrorUX", "index.html"), "utf8"))).toContain("AgaveErrorUX.js");
    expect((await stat(join(a, "office-js", "AgaveErrorUX", "images", "moe_status_icons.png"))).size).toBeGreaterThan(50);
    expect(JSON.parse(await readFile(join(a, "SOURCE.json"), "utf8")).office_js_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect((await readFile(join(a, "LICENSE-office-js.md"), "utf8")).length).toBeGreaterThan(10);
    expect(await readdir(join(dist, "outlook"))).toEqual(["assets"]);         // временный каталог убран
  });

  it("первый источник не отвечает — берётся второй целиком; набор с двух источников не смешивается", async () => {
    const dist = await tmp();
    const a = "https://a.example.org/", b = "https://b.example.org/";
    const f = vi.fn(async (url) => {
      const u = String(url);
      if (u.startsWith(a) && !u.endsWith("office.js")) throw new Error("reset");          // первый источник отдаёт office.js, но остальное не получает
      if (u.startsWith(a)) return new Response(OFFICE.replace("Office JavaScript", "Office JavaScript "), { status: 200 });
      return cdn(FULL(), b)(u);
    });
    vi.stubGlobal("fetch", f);
    const info = await vendor(dist, () => undefined, [a, b]);
    expect(info?.source).toBe(b);
    expect(await readFile(join(dist, "outlook", "assets", "office-js", "office.js"), "utf8")).toBe(OFFICE);
  }, 60000);

  it("сбой: сборка не падает, каталога assets нет, остатков нет, предупреждение называет причину", async () => {
    const dist = await tmp();
    const log = vi.fn();
    vi.stubGlobal("fetch", cdn(FULL({ "ru-ru/outlook_strings.js": "<html>proxy</html>" + " ".repeat(300) }), "https://cdn-a.example.org/1/"));
    expect(await vendor(dist, log, ["https://cdn-a.example.org/1/"])).toBeNull();
    expect(await readdir(join(dist, "outlook")).catch(() => [])).toEqual([]);
    expect(log.mock.calls.join("\n")).toMatch(/не создана.*обязательные файлы.*ru-ru\/outlook_strings\.js/s);
  });

  it("повторный запуск при сбое убирает прежнюю неполную копию, при успехе — заменяет целиком", async () => {
    const dist = await tmp();
    vi.stubGlobal("fetch", cdn(FULL(), "https://cdn-a.example.org/1/"));
    await vendor(dist, () => undefined, ["https://cdn-a.example.org/1/"]);
    vi.stubGlobal("fetch", cdn({}, "https://cdn-a.example.org/1/"));
    expect(await vendor(dist, () => undefined, ["https://cdn-a.example.org/1/"])).toBeNull();
    expect(await readdir(join(dist, "outlook"))).toEqual([]);                 // нет «наполовину обновлённой» копии, страницы переходят на запасной путь
  }, 60000);
});
