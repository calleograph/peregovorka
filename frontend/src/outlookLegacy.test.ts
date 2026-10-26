import { readFileSync } from "node:fs";
import vm from "node:vm";
import { parseAst } from "rolldown/parseAst";
import { describe, expect, it } from "vitest";

// Надстройка Outlook в самом старом поддерживаемом окружении: классический Outlook 2016/2019 с локальным Exchange показывает её в Internet Explorer 11
// (нет Promise и fetch; сайт во внутренней сети IE открывает в режиме совместимости). Настоящего IE здесь нет — проверяются условия, без которых он
// гарантированно не работает: синтаксис ES5, порядок подключения, запросы через XMLHttpRequest, полифил Promise, самодиагностика запуска.
const dir = new URL("../public/outlook/", import.meta.url);
const read = (f: string) => readFileSync(new URL(f, dir), "utf-8");
const SCRIPTS = ["boot.js", "polyfill.js", "ms-loader.js", "ms-fallback.js", "commands.js", "taskpane.js", "auth.js"];

describe("надстройка Outlook: Internet Explorer 11 и режим совместимости", () => {
  it("все скрипты надстройки — ES5 (иначе IE 11 не разберёт файл, и Office не запустит надстройку)", () => {
    // разбор парсером сборщика (ESTree): ищем всё, чего нет в ES5
    const banned = new Set(["ArrowFunctionExpression", "TemplateLiteral", "TaggedTemplateExpression", "ClassDeclaration", "ClassExpression", "SpreadElement",
      "ForOfStatement", "ObjectPattern", "ArrayPattern", "AssignmentPattern", "RestElement", "AwaitExpression", "YieldExpression", "ChainExpression", "ImportExpression"]);
    for (const f of SCRIPTS) {
      const found: string[] = [];
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const walk = (n: any) => {
        if (!n || typeof n !== "object") return;
        if (Array.isArray(n)) { n.forEach(walk); return; }
        if (typeof n.type === "string") {
          if (banned.has(n.type)) found.push(`${n.type}@${n.start}`);
          if (n.type === "VariableDeclaration" && n.kind !== "var") found.push(`${n.kind}@${n.start}`);
          if ((n.type === "FunctionDeclaration" || n.type === "FunctionExpression") && (n.async || n.generator)) found.push(`async/generator@${n.start}`);
          if (n.type === "Property" && (n.shorthand || n.method || n.computed)) found.push(`property-es6@${n.start}`);
          if (n.type === "LogicalExpression" && n.operator === "??") found.push(`??@${n.start}`);
        }
        for (const k of Object.keys(n)) if (k !== "parent") walk(n[k]);
      };
      walk(parseAst(read(f)));
      expect(found, f).toEqual([]);
    }
  });

  it("страницы: X-UA-Compatible сразу после charset; boot.js и Promise до Office.js", () => {
    for (const f of ["taskpane.html", "commands.html", "auth.html"]) {
      const h = read(f);
      expect(h, f).toMatch(/<meta charset="utf-8">\s*<meta http-equiv="X-UA-Compatible" content="IE=edge">/);
      const boot = h.indexOf("/outlook/boot.js"), poly = h.indexOf("/outlook/polyfill.js"), office = h.indexOf("/outlook/ms-loader.js");
      expect(boot > 0 && boot < poly && poly < office, f).toBe(true);
      expect(h, f).not.toMatch(/<script[^>]+src="https?:\/\//);                 // библиотеки Microsoft подключает только ms-loader.js: источник выбирается в одном месте
    }
    expect(read("taskpane.html")).not.toMatch(/<use href="[^"]+"\/>/);          // у каждого значка есть xlink:href (IE 11)
  });

  /** Запуск ms-loader.js / ms-fallback.js в подставной среде: собирает всё, что скрипты пишут через document.write. */
  function loadLibs(search: string, hasOffice: boolean) {
    const written: string[] = [];
    const w: Record<string, unknown> = { location: { search } };
    if (hasOffice) w.Office = {};
    const ctx = vm.createContext({ window: w, document: { write: (s: string) => written.push(s) } });
    vm.runInContext(read("ms-loader.js"), ctx);
    const first = [...written];
    if (written.some((t) => t.includes("ms-fallback.js"))) vm.runInContext(read("ms-fallback.js"), ctx);          // выполняется после локального office.js, только если его написали
    return { first, all: written, office: w.PG_OFFICE as { mode: string; src: string } };
  }
  const srcs = (a: string[]) => a.map((t) => /src="([^"]+)"/.exec(t)![1]);

  it("ms-loader: по умолчанию Office.js с этого сервера (MicrosoftAjax не подключается: он ломается под CSP), CDN только запасной путь", () => {
    const ok = loadLibs("", true);
    expect(srcs(ok.first)).toEqual(["/outlook/assets/office-js/office.js", "/outlook/ms-fallback.js"]);
    expect(srcs(ok.all)).toHaveLength(2);                                    // Office есть — запасной путь не нужен
    expect(ok.office).toEqual({ mode: "local", src: "local" });
    const lost = loadLibs("?et=1", false);                                    // локальный office.js не загрузился (нет файла в образе)
    expect(srcs(lost.all).pop()).toBe("https://officeapis.public.onecdn.static.microsoft/1/office.js");
    expect(lost.office.src).toBe("cdn_fallback");
  });

  it("ms-loader: режимы cdn и cdn_only (в обоих — только Office.js); неизвестное значение и посторонние параметры безопасно дают local", () => {
    expect(srcs(loadLibs("?officejs=cdn", false).all)).toEqual(["https://officeapis.public.onecdn.static.microsoft/1/office.js"]);
    expect(srcs(loadLibs("?a=1&officejs=cdn_only", false).all)).toEqual(["https://appsforoffice.microsoft.com/lib/1/hosted/office.js"]);
    for (const q of ["?officejs=evil", "?officejs=%22%3E%3Cscript%3E", "?xofficejs=cdn"]) {
      const r = loadLibs(q, true);
      expect(r.office.mode, q).toBe("local");
      expect(srcs(r.all).every((s) => s.startsWith("/outlook/")), q).toBe(true);     // в document.write не попадает ничего из адреса страницы
    }
  });

  it("CSS: у каждого значения через var() есть обычное значение перед ним (IE 11 не знает переменных)", () => {
    const css = read("outlook.css");
    const re = /(?<![-\w])([a-z][a-z-]*)\s*:\s*([^;{}]*var\(--[^;{}]*)/g;
    let m: RegExpExecArray | null, n = 0;
    while ((m = re.exec(css))) {
      const before = css.slice(Math.max(0, m.index - 400), m.index);
      expect(before, m[0]).toMatch(new RegExp(`${m[1]}\\s*:\\s*[^;]*;\\s*$`));
      n++;
    }
    expect(n).toBeGreaterThan(20);
  });

  /** Контекст «IE 11»: своего Promise и fetch нет; XMLHttpRequest — подставной, отвечает по сценарию. */
  function ie11(responder: (req: { method: string; url: string; headers: Record<string, string>; body: string | null }) => { status: number; text: string } | null) {
    const sent: Array<{ method: string; url: string; headers: Record<string, string>; body: string | null }> = [];
    class FakeXHR {
      method = ""; url = ""; headers: Record<string, string> = {}; status = 0; responseText = ""; timeout = 0;
      onload: (() => void) | null = null; onerror: (() => void) | null = null; ontimeout: (() => void) | null = null;
      open(m: string, u: string) { this.method = m; this.url = u; }
      setRequestHeader(k: string, v: string) { this.headers[k] = v; }
      send(body: string | null) {
        const req = { method: this.method, url: this.url, headers: this.headers, body };
        sent.push(req);
        const r = responder(req);
        if (r) setTimeout(() => { this.status = r.status; this.responseText = r.text; this.onload?.(); }, 1);
        else if (this.timeout) setTimeout(() => this.ontimeout?.(), this.timeout);
      }
    }
    const ctx: Record<string, unknown> = { setTimeout, clearTimeout, XMLHttpRequest: FakeXHR, Math, Date, JSON, localStorage: undefined };
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext("this.Promise = undefined; this.fetch = undefined;", ctx);
    vm.runInContext(read("polyfill.js"), ctx);
    vm.runInContext(read("commands.js"), ctx);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return { pg: ctx.PGOutlook as any, P: ctx.Promise as any, sent };
  }

  it("Promise-полифил: цепочки, ошибки, all и race — как у встроенного", async () => {
    const { P } = ie11(() => null);
    const v = await new Promise((res) => P.resolve(1).then((x: number) => x + 1).then((x: number) => { throw new Error(String(x)); })
      .catch((e: Error) => Number(e.message) * 10).then(res));
    expect(v).toBe(20);
    expect(await new Promise((res) => P.all([1, P.resolve(2), new P((r: (x: number) => void) => setTimeout(() => r(3), 5))]).then(res))).toEqual([1, 2, 3]);
    expect(await new Promise((res) => P.race([new P((r: (x: string) => void) => setTimeout(() => r("медленно"), 30)), P.resolve("быстро")]).then(res))).toBe("быстро");
    const order: string[] = [];
    P.resolve().then(() => order.push("потом")); order.push("сразу");          // продолжения — асинхронно
    await new Promise((r) => setTimeout(r, 5));
    expect(order).toEqual(["сразу", "потом"]);
  });

  it("без fetch запросы к API идут через XMLHttpRequest: заголовки, тело, ответ и ошибки сервера", async () => {
    const { pg, sent } = ie11((req) => req.url.endsWith("/me") ? { status: 200, text: '{"user":"ivanov"}' }
      : { status: 422, text: '{"detail":{"code":"in_past","message":"Встреча уже закончилась"}}' });
    const me = await new Promise((res, rej) => pg.whoami("pgo_test").then(res, rej));
    expect(me).toEqual({ user: "ivanov" });
    expect(sent[0].headers.Authorization).toBe("Bearer pgo_test");
    const err = await new Promise((res) => pg.requestRoom({ subject: "Планёрка" }, "pgo_test").then(null, res)) as { code: string };
    expect(err.code).toBe("in_past");
    expect(JSON.parse(sent[1].body as string)).toEqual({ subject: "Планёрка" });
  });

  it("без fetch: сервер не ответил — понятная ошибка по пределу времени, а не вечное ожидание", async () => {
    const { pg } = ie11(() => null);
    pg.T.api = 30;
    const err = await new Promise((res) => pg.whoami("pgo_test").then(null, res)) as { code: string };
    expect(err.code).toBe("timeout");
  });

  /** boot.js в «IE 7»: нет addEventListener, JSON, textContent — только то, что было в старом IE. */
  function ie7(dm: number, withOffice: boolean) {
    const beacons: string[] = [];
    const timers: Array<() => void> = [];
    const appended: string[] = [];
    const box = { id: "", className: "", style: {} as Record<string, string>, firstChild: null as unknown,
      removeChild() { this.firstChild = null; }, appendChild(n: { text: string }) { appended.push(n.text); this.firstChild = n; } };
    const doc = { documentMode: dm, location: { pathname: "/outlook/taskpane.html" }, body: { appendChild: () => undefined },
      getElementById: () => null, createElement: () => box, createTextNode: (t: string) => ({ text: t }) };
    const ctx: Record<string, unknown> = { document: doc, Math, Date, encodeURIComponent, String,
      Image: function (this: { src: string }) { Object.defineProperty(this, "src", { set: (v: string) => beacons.push(v) }); },
      setTimeout: (f: () => void) => timers.push(f) };
    ctx.window = ctx;
    if (withOffice) ctx.Office = { context: {} };
    vm.createContext(ctx);
    vm.runInContext("this.JSON = undefined; this.Promise = undefined; this.fetch = undefined;", ctx);
    vm.runInContext(read("boot.js"), ctx);
    return { beacons, timers, appended, ctx };
  }

  it("самодиагностика: маячок сразу при открытии — даже в режиме совместимости IE 7", () => {
    const { beacons } = ie7(7, false);
    expect(beacons[0]).toMatch(/^\/api\/v1\/outlook\/diag\?e=boot&pg=taskpane&/);
    expect(beacons[0]).toContain("dm=7");
    expect(beacons[0]).toContain("p=undefined");
    expect(beacons[0]).toContain("o=undefined");
  });

  it("самодиагностика: Office не запустил надстройку за 10 с — причина на странице и на сервере", () => {
    const compat = ie7(7, false);
    compat.timers[0]();
    expect(compat.appended[0]).toContain("режиме совместимости Internet Explorer 7");
    expect(compat.beacons[1]).toContain("e=init_timeout");
    const noOffice = ie7(11, false);
    noOffice.timers[0]();
    expect(noOffice.appended[0]).toContain("Office.js не загрузилась");
    const started = ie7(11, true);
    (started.ctx.PG_BOOT as { ready: boolean }).ready = true;           // taskpane.js отметил запуск — сторож молчит
    started.timers[0]();
    expect(started.appended).toEqual([]);
  });
});
