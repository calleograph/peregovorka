// Проверки приложения как у пользователя — запуск из исходников (--app dev) или собранной программы (--app <путь к exe>), всегда с ПУСТЫМ профилем.
// Не заменяет ручную приёмку (docs/DESKTOP_ACCEPTANCE.md): камеры, AD, Outlook, SmartScreen здесь не проверяются.
//
//   node scripts/check.mjs firstrun --app dev|<exe> [--out dir] [--untrusted-pfx f.pfx]
//       первый запуск: приветствие (не «not found», не пустое окно), шаг сервера, ошибки адреса (и недоверенного сертификата, если дан PFX),
//       все вкладки настроек до входа, тема; ни одной ошибки в консоли оболочки и в журнале главного процесса
//   node scripts/check.mjs offline  --app ...   две организации без доступного сервера: переключение, состояние «нет связи», настройки, диагностика
//   node scripts/check.mjs shots    --app ... [--out dir]   снимки локальных экранов: 1280×800 и 800×520, масштаб 100/150/200 %, светлая и тёмная тема;
//       проверка вёрстки: нет горизонтальной прокрутки, обрезанного текста, наложений элементов, разных высот однотипных кнопок и полей
//   node scripts/check.mjs update   --app <exe> --pfx feed.pfx --feed dir --next X.Y.Z   (CI Windows) проверить → скачать → установить
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, createReadStream, existsSync } from "node:fs";
import https from "node:https";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { launch, page, sleep, ROOT } from "./cdp.mjs";

const [mode, ...rest] = process.argv.slice(2);
const opt = (k, d) => { const i = rest.indexOf(`--${k}`); return i >= 0 ? rest[i + 1] : d; };
const APP = opt("app", "dev");
const OUT = opt("out", join(ROOT, "out", "check"));
const PASS = "pg-e2e";                                     // пароль временных PFX испытаний (создаются на машине проверки и удаляются с ней)
mkdirSync(OUT, { recursive: true });
let failed = 0;
const check = (name, ok, extra = "") => { console.log(ok ? `OK   ${name}` : `FAIL ${name}  [${String(extra).replace(/\s+/g, " ").slice(0, 500)}]`); if (!ok) failed++; };
const profile = mkdtempSync(join(tmpdir(), "pg-profile-"));

function serve(port, pfx, root) {
  const srv = https.createServer({ pfx: readFileSync(pfx), passphrase: PASS }, (req, res) => {
    const f = root ? join(root, basename(decodeURIComponent(new URL(req.url, "https://x").pathname))) : null;
    if (!f || !existsSync(f)) { res.writeHead(root ? 404 : 200); res.end("{}"); return; }
    res.writeHead(200, { "content-type": "application/octet-stream" });
    createReadStream(f).pipe(res);
  });
  return new Promise((r) => srv.listen(port, "127.0.0.1", () => r(srv)));
}

async function probe(s, url) {
  await s.js(`(() => { document.querySelector('#srv').value = ${JSON.stringify(url)}; document.querySelector('form').requestSubmit(); })()`);
  await sleep(300);
  await s.until("!document.querySelector('form button[type=submit]')?.disabled", 40);
  await sleep(300);
  return s.text();
}

/** Ошибки журнала главного процесса этого запуска (ожидаемые предупреждения проверок адреса — уровня WARN и сюда не попадают). */
function mainLogErrors() {
  try { return readFileSync(join(profile, "logs", "main.log"), "utf8").split("\n").filter((l) => / ERROR /.test(l)); } catch { return ["журнал main.log не создан"]; }
}

const WELCOME = "document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'";

async function start(extra) {
  const app = launch(APP, { profile, extra });
  app.on("exit", (code) => { if (code) console.log(`приложение завершилось с кодом ${code}`); });
  const s = await page();
  await s.watchErrors();
  await s.send("Page.enable");
  await s.send("Page.reload", { ignoreCache: true });                         // ошибки загрузки — с самого начала, под наблюдением
  await sleep(500);
  return { app, s };
}

// -------------------------------------------------------------------------------------------- первый запуск
// запись автозапуска проверяется только в одноразовом окружении CI (на компьютере разработчика проверочный запуск автозапуск не трогает)
const RUN_KEY = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run";
const AUTOSTART_REG = process.platform === "win32" && !!process.env.CI && APP !== "dev";
if (AUTOSTART_REG) process.env.PEREGOVORKA_AUTOSTART_TEST = "1";

async function firstrun() {
  const pfx = opt("untrusted-pfx");
  const untrusted = pfx ? await serve(18443, pfx, null) : null;
  const { app, s } = await start();
  try {
    check("первый запуск: «Добро пожаловать в Peregovorka»", await s.until(WELCOME, 20), await s.js("document.body.innerText.slice(0, 160)"));
    check("в окне нет «not found», оболочка из пакета", !(await s.js("document.body.innerText")).includes("not found") && (await s.js("location.href")) === "app://shell/index.html");
    check("стили и шрифты загружены", await s.js("getComputedStyle(document.querySelector('.btn.primary')).backgroundColor !== 'rgba(0, 0, 0, 0)' && document.fonts.check('16px Inter')"));
    await s.shot(join(OUT, "01-welcome.png"));
    check("«Добавить организацию» → шаг сервера", (await s.click("Добавить организацию")) && (await s.until("!!document.querySelector('#srv')", 5)));
    let t = await probe(s, "http://meet.example.org");
    check("HTTP-адрес отклоняется понятным сообщением", t.includes("защищённый адрес https"), t.slice(-160));
    t = await probe(s, "https://127.0.0.1:18999");
    check("недоступный сервер: сообщение в мастере", t.includes("Сервер недоступен"), t.slice(-200));
    await s.shot(join(OUT, "02-unreachable.png"));
    if (untrusted) {
      t = await probe(s, "https://localhost:18443");
      check("недоверенный сертификат: отказ и «кем выдан»", t.includes("Сертификат не доверен") && t.includes("Peregovorka E2E Untrusted"), t.slice(-300));
      await s.shot(join(OUT, "03-untrusted-cert.png"));
    } else console.log("—    недоверенный сертификат: пропущено (нет --untrusted-pfx)");
    check("«Назад» — к приветствию", (await s.click("Назад")) && (await s.until(WELCOME, 5)));
    await s.click("Настройки");
    await s.until("!!document.querySelector('.tabs')", 5);
    for (const tab of ["general", "notify", "orgs", "network", "updates", "diag", "about"]) {
      await s.js(`document.querySelector('#tab-${tab}').click()`);
      await sleep(tab === "diag" ? 1500 : 400);
      check(`настройки до входа: «${tab}»`, (await s.js("document.querySelector('.panel h2')?.textContent ?? ''")).length > 0);
      if (tab === "diag") {
        await s.until("document.body.innerText.includes('в порядке')", 20);              // отчёт собирается асинхронно: на медленной машине дольше фиксированных 1,5 с
        const d = await s.text(); check("диагностика: файлы программы в порядке", d.includes("в порядке"), d.slice(0, 400));
      }
      await s.shot(join(OUT, `04-settings-${tab}.png`));
    }
    await s.js("document.querySelector('#tab-general').click()");
    await sleep(400);
    check("автозапуск у нового профиля по умолчанию — «Свёрнутым в трей»", await s.js("[...document.querySelectorAll('.seg [aria-pressed=true]')].some((b) => b.textContent.trim() === 'Свёрнутым в трей')"));
    if (AUTOSTART_REG) {
      const run = (() => { try { return execFileSync("reg.exe", ["query", RUN_KEY, "/v", "Peregovorka"], { encoding: "utf8" }); } catch { return ""; } })();
      check("Windows: запись автозапуска создана (в трей, ключ --hidden, путь к программе)", /Peregovorka\.exe/i.test(run) && run.includes("--hidden"), run.trim().slice(-200));
    }
    await s.js("window.pg.call('shell:settings', { theme: 'dark' })");
    await s.js("document.querySelector('#tab-general').click()");
    await sleep(600);
    check("тёмная тема применяется и отмечена", await s.js("matchMedia('(prefers-color-scheme: dark)').matches && document.querySelector('.seg [aria-pressed=true]')?.textContent !== undefined"));
    await s.shot(join(OUT, "05-dark.png"));
    await s.js("window.pg.call('shell:settings', { theme: 'system', closeAction: 'quit' })");
    const errs = s.errors();
    check("консоль оболочки без ошибок (загрузка, исключения, console.error)", errs.length === 0, errs.join(" | "));
    const logErr = mainLogErrors();
    check("журнал главного процесса без ошибок (preload, протокол, отрисовка)", logErr.length === 0, logErr.join(" | "));
    s.close();
  } finally {
    app.kill();
    untrusted?.close();
    if (AUTOSTART_REG) try { execFileSync("reg.exe", ["delete", RUN_KEY, "/v", "Peregovorka", "/f"], { stdio: "ignore" }); } catch { /* записи не было */ }
  }
}

// -------------------------------------------------------------------------------------------- без сервера
async function offline() {
  // две организации, заданные политикой, на адресах, где сервера нет (TEST-NET-1, RFC 5737)
  const servers = "Головной офис|https://192.0.2.1;Филиал «Северо-Западный» с длинным названием|https://192.0.2.2";
  const regKey = "HKCU\\SOFTWARE\\Policies\\Peregovorka";
  let extra = [];
  if (process.platform === "win32") execFileSync("reg.exe", ["add", regKey, "/v", "Servers", "/t", "REG_SZ", "/d", servers, "/f"]);
  else { const f = join(profile, "policy.json"); writeFileSync(f, JSON.stringify({ Servers: servers })); process.env.PEREGOVORKA_POLICY_FILE = f; }
  const { app, s } = await start(extra);
  try {
    check("две организации без сервера — в боковой панели", await s.until("document.querySelectorAll('.side .orgs .org').length === 2", 20));
    check("активная организация: «подключение» или «нет связи», не пустое окно", await s.until("/Подключение|Нет связи|Сервер/.test(document.querySelector('main').innerText)", 60),
      await s.text());
    await s.shot(join(OUT, "10-offline-org.png"));
    await s.js("document.querySelectorAll('.side .orgs .org')[1].click()");
    check("переключение на вторую организацию", await s.until("document.querySelectorAll('.side .orgs .org')[1].getAttribute('aria-current') === 'true'", 5));
    check("у второй — своё состояние с её названием", await s.until("document.querySelector('main').innerText.includes('Филиал')", 10), await s.text());
    await s.shot(join(OUT, "11-offline-switch.png"));
    await s.js("document.querySelector('.side .tool[aria-label=\"Настройки\"]').click()");
    await s.js("document.querySelector('#tab-orgs').click()");
    await sleep(500);
    const orgs = await s.text();
    check("настройки → организации: обе, «нет связи» или «подключение», заданы администратором", orgs.includes("Головной офис") && orgs.includes("задано администратором"), orgs.slice(0, 400));
    await s.js("document.querySelector('#tab-diag').click()");
    await sleep(1500);
    check("диагностика открывается без сервера", (await s.text()).includes("192.0.2.1"), (await s.text()).slice(0, 300));
    await s.shot(join(OUT, "12-offline-diag.png"));
    const errs = s.errors();
    check("консоль оболочки без ошибок", errs.length === 0, errs.join(" | "));
    s.close();
  } finally {
    app.kill();
    if (process.platform === "win32") execFileSync("reg.exe", ["delete", regKey, "/f"]);
  }
}

// -------------------------------------------------------------------------------------------- снимки и вёрстка
const LAYOUT = `(() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = { hscroll: document.documentElement.scrollWidth > innerWidth + 1 || [...document.querySelectorAll('main, .panel, .page')].some((e) => e.scrollWidth > e.clientWidth + 1),
    cut: [], heights: {}, unnamed: [], overlap: [], scrollers: [] };
  // внутренняя горизонтальная прокрутка (кроме намеренных: журнал, ввод) — в узком окне элементы должны переноситься, а не уезжать за край
  for (const e of document.querySelectorAll('body *')) {
    if (!vis(e) || e.matches('pre, input, textarea, .seg, .grid')) continue;
    const s = getComputedStyle(e);
    if ((s.overflowX === 'auto' || s.overflowX === 'scroll') && e.scrollWidth > e.clientWidth + 1) out.scrollers.push(e.tagName.toLowerCase() + '.' + [...e.classList].join('.'));
  }
  for (const e of document.querySelectorAll('body *')) {
    if (!vis(e) || e.closest('svg')) continue;
    const s = getComputedStyle(e);
    const texts = [...e.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim());
    if (!texts.length || s.textOverflow === 'ellipsis' || !(s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflow === 'clip')) continue;
    const box = e.getBoundingClientRect();
    const cut = texts.some((n) => { const r = document.createRange(); r.selectNodeContents(n); const t = r.getBoundingClientRect(); return t.right > box.right + 1 || t.bottom > box.bottom + 2; });
    if (cut) out.cut.push(e.tagName.toLowerCase() + '.' + [...e.classList].join('.') + ': ' + e.textContent.trim().slice(0, 40));
  }
  for (const [sel, key] of [['.btn:not(.sm):not(.icon):not(.lg)', 'кнопки'], ['.input', 'поля'], ['.seg', 'переключатели'], ['.tabs button', 'вкладки']]) {
    const hs = [...document.querySelectorAll(sel)].filter(vis).map((e) => Math.round(e.getBoundingClientRect().height));
    if (hs.length) out.heights[key] = [...new Set(hs)];
  }
  const ctl = [...document.querySelectorAll('button, a[href], input, select, .seg, .org')].filter(vis);
  for (const e of ctl) {
    const name = (e.getAttribute('aria-label') || e.getAttribute('title') || e.textContent || e.getAttribute('placeholder') || (e.labels && e.labels[0] && e.labels[0].textContent) || '').trim();
    if (!name && e.matches('button, a, input')) out.unnamed.push(e.tagName.toLowerCase() + '.' + [...e.classList].join('.'));
  }
  for (let i = 0; i < ctl.length; i++) for (let j = i + 1; j < ctl.length; j++) {
    const a = ctl[i], b = ctl[j];
    if (a.contains(b) || b.contains(a)) continue;
    const r = a.getBoundingClientRect(), q = b.getBoundingClientRect();
    const w = Math.min(r.right, q.right) - Math.max(r.left, q.left), h = Math.min(r.bottom, q.bottom) - Math.max(r.top, q.top);
    if (w > 2 && h > 2) out.overlap.push((a.textContent || a.className).trim().slice(0, 30) + ' × ' + (b.textContent || b.className).trim().slice(0, 30));
  }
  return out;
})()`;

function judge(name, r) {
  const bad = [];
  if (r.hscroll) bad.push("горизонтальная прокрутка");
  if (r.cut.length) bad.push("обрезано: " + r.cut.slice(0, 3).join("; "));
  for (const [k, v] of Object.entries(r.heights)) if (v.length > 1) bad.push(`разная высота (${k}): ${v.join("/")}`);
  if (r.unnamed.length) bad.push("без названия: " + r.unnamed.slice(0, 3).join(", "));
  if (r.overlap.length) bad.push("наложения: " + r.overlap.slice(0, 3).join("; "));
  if (r.scrollers.length) bad.push("горизонтальная прокрутка внутри: " + r.scrollers.slice(0, 3).join(", "));
  check(name, !bad.length, bad.join(" · "));
}

async function shots() {
  const { app, s } = await start();
  try {
    await s.until(WELCOME, 20);
    const screens = [
      ["welcome", async () => { await s.js("window.pg.call('shell:page', { page: 'add' })"); if (!(await s.js(WELCOME))) await s.click("Назад"); }],
      ["server-error", async () => {
        await s.click("Добавить организацию"); await s.until("!!document.querySelector('#srv')", 5);
        await s.js("document.querySelector('#nm').value = 'Управление информационных технологий и связи головного офиса'");
        await probe(s, "https://127.0.0.1:18999");
      }],
      ...["general", "updates", "diag", "about"].map((t) => [`settings-${t}`, async () => {
        if (!(await s.js("!!document.querySelector('.tabs')"))) await s.js("document.querySelector('.side .tool[aria-label=\"Настройки\"]').click()");
        await s.js(`document.querySelector('#tab-${t}').click()`); await sleep(t === "diag" ? 1500 : 300);
      }]),
    ];
    for (const theme of ["light", "dark"]) {
      await s.js(`window.pg.call('shell:settings', { theme: '${theme}' })`);
      await sleep(300);
      for (const [name, open] of screens) {
        await s.send("Emulation.clearDeviceMetricsOverride");
        await open();
        for (const [w, h, scale] of [[1280, 800, 1], [800, 520, 1], [1280, 800, 1.5], [1280, 800, 2]]) {
          // масштаб Windows 150/200 % — окно того же размера в пикселях экрана содержит в 1,5–2 раза меньше CSS-пикселей
          await s.send("Emulation.setDeviceMetricsOverride", { width: Math.round(w / scale), height: Math.round(h / scale), deviceScaleFactor: scale, mobile: false });
          await sleep(250);
          const key = `${theme}-${name}-${w}x${h}@${scale * 100}`;
          await s.shot(join(OUT, `v-${key}.png`));
          judge(`вёрстка ${key}`, await s.js(LAYOUT));
        }
      }
      await s.send("Emulation.clearDeviceMetricsOverride");
      await s.js("document.querySelector('.side .tool[aria-label=\"Подключить организацию\"]')?.click()");
      await s.click("Назад");
    }
    const errs = s.errors();
    check("консоль оболочки без ошибок", errs.length === 0, errs.join(" | "));
    s.close();
  } finally { app.kill(); }
  console.log(`снимки: ${OUT}`);
}

// -------------------------------------------------------------------------------------------- обновление (CI Windows)
async function update() {
  const feed = await serve(8443, opt("pfx"), opt("feed"));
  const next = opt("next");
  const app = launch(APP);                                    // профиль установленного приложения: проверяется сохранность его данных
  try {
    const s = await page();
    await s.js("window.pg.call('shell:settings', { theme: 'dark', closeAction: 'quit' })");
    await s.click("Настройки");
    await s.until("!!document.querySelector('#tab-updates')", 5);
    await s.js("document.querySelector('#tab-updates').click()");
    await sleep(500);
    await s.click("Проверить обновления");
    check(`проверка обновлений: найдена ${next} (подпись манифеста верна)`, await s.until(`document.querySelector('main').innerText.includes('Доступна версия ${next}')`, 40), (await s.text()).slice(0, 400));
    await s.shot(join(OUT, "20-update-available.png"));
    await s.click("Скачать обновление");
    check("загрузка: размер, SHA-256 и подпись Authenticode установщика проверены", await s.until("document.querySelector('main').innerText.includes('загружена и проверена')", 180), (await s.text()).slice(0, 400));
    await s.shot(join(OUT, "21-update-ready.png"));
    await s.click("Установить обновление");
    const exited = await new Promise((r) => { const t = setTimeout(() => r(false), 30000); app.once("exit", () => { clearTimeout(t); r(true); }); });
    check("приложение закрылось для установки (установщик запущен)", exited);
    s.close();
  } finally {
    if (app.exitCode === null) app.kill();
    feed.close();
  }
}

try {
  const run = { firstrun, offline, shots, update }[mode];
  if (!run) throw new Error("режим: firstrun | offline | shots | update");
  await run();
} catch (e) {
  check("проверка выполнена без исключений", false, e?.stack ?? e);
}
await sleep(500);
try { rmSync(profile, { recursive: true, force: true }); } catch { /* занят завершающимся процессом */ }
console.log(failed ? `НЕ ПРОШЛИ: ${failed}` : "все проверки пройдены");
process.exit(failed ? 1 : 0);
