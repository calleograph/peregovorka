// Испытание УСТАНОВЛЕННОГО приложения на Windows (CI, windows-runner): настоящий Peregovorka.exe из установщика, управление окном через CDP
// (--remote-debugging-port), снимки каждого экрана. Не заменяет ручную приёмку: камеры, AD, Outlook и SmartScreen здесь не проверяются.
//
//   node scripts/win-e2e.mjs firstrun <Peregovorka.exe> <каталог снимков> <untrusted.pfx>
//       первый запуск (приветствие, не «not found»), шаг сервера, ошибки адреса и сертификата, настройки, тёмная тема
//   node scripts/win-e2e.mjs update <Peregovorka.exe> <каталог снимков> <feed.pfx> <каталог канала> <следующая версия>
//       проверить → скачать → установить: приложение закрывается, установщик обновляет его поверх и запускает снова
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, createReadStream, existsSync } from "node:fs";
import https from "node:https";
import { join, basename } from "node:path";

const [mode, exe, out, pfx, feedDir, nextVersion] = process.argv.slice(2);
const PORT = 9333;
const PASS = "pg-e2e";                                     // пароль временных PFX испытания (сертификаты создаются в шаге CI и удаляются с машиной)
mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (name, ok, extra = "") => { console.log(ok ? `OK   ${name}` : `FAIL ${name}  [${String(extra).replace(/\s+/g, " ").slice(0, 400)}]`); if (!ok) failed++; };

function serve(port, root) {
  const srv = https.createServer({ pfx: readFileSync(pfx), passphrase: PASS }, (req, res) => {
    const f = root ? join(root, basename(decodeURIComponent(new URL(req.url, "https://x").pathname))) : null;
    if (!f || !existsSync(f)) { res.writeHead(root ? 404 : 200); res.end("{}"); return; }
    res.writeHead(200, { "content-type": "application/octet-stream" });
    createReadStream(f).pipe(res);
  });
  return new Promise((r) => srv.listen(port, "127.0.0.1", () => r(srv)));
}

async function shell() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const t = list.find((x) => x.url.startsWith("app://shell/index.html"));
      if (t) return connect(t.webSocketDebuggerUrl);
    } catch { /* ещё запускается */ }
    await sleep(500);
  }
  throw new Error("окно приложения не появилось за 30 с");
}

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0;
  const wait = new Map();
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } };
  const send = (method, params = {}) => new Promise((r) => { const n = ++id; wait.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
  const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
  return {
    js,
    close: () => ws.close(),
    async until(expr, sec = 15) { for (let i = 0; i < sec * 4; i++) { if (await js(expr).catch(() => false)) return true; await sleep(250); } return false; },
    async shot(name) { const r = await send("Page.captureScreenshot", { format: "png" }); if (r.result?.data) writeFileSync(join(out, `${name}.png`), Buffer.from(r.result.data, "base64")); },
    click: (text) => js(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().includes(${JSON.stringify(text)}) && !b.disabled); if (b) b.click(); return !!b; })()`),
    text: () => js("document.querySelector('main')?.innerText ?? ''"),
  };
}

function launch() {
  const p = spawn(exe, [`--remote-debugging-port=${PORT}`], { stdio: "ignore" });
  p.on("exit", (code) => console.log(`приложение завершилось: ${code}`));
  return p;
}

async function probe(s, url) {
  await s.js(`(() => { document.querySelector('#srv').value = ${JSON.stringify(url)}; document.querySelector('form').requestSubmit(); })()`);
  await sleep(300);
  await s.until("!document.querySelector('form button[type=submit]')?.disabled", 40);
  await sleep(300);
  return s.text();
}

async function firstrun() {
  const untrusted = await serve(18443, null);
  const app = launch();
  try {
    const s = await shell();
    check("первый запуск: «Добро пожаловать в Peregovorka»", await s.until("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 20), await s.js("document.body.innerText.slice(0, 120)"));
    check("в окне нет «not found», оболочка из пакета", !(await s.js("document.body.innerText")).includes("not found") && (await s.js("location.href")) === "app://shell/index.html");
    check("оформление загружено (шрифт и стили)", await s.js("getComputedStyle(document.querySelector('.btn.primary')).backgroundColor !== 'rgba(0, 0, 0, 0)'"));
    await s.shot("01-welcome");
    check("«Добавить организацию» → шаг сервера", (await s.click("Добавить организацию")) && (await s.until("!!document.querySelector('#srv')", 5)));
    await s.shot("02-server-step");
    let t = await probe(s, "http://meet.example.org");
    check("HTTP-адрес отклоняется понятным сообщением", t.includes("защищённый адрес https"), t.slice(-160));
    t = await probe(s, "https://127.0.0.1:18999");
    check("недоступный сервер: сообщение остаётся в мастере", t.includes("Сервер недоступен"), t.slice(-200));
    await s.shot("03-unreachable");
    t = await probe(s, "https://localhost:18443");
    check("недоверенный сертификат: отказ и «кем выдан»", t.includes("Сертификат не доверен") && t.includes("Peregovorka E2E Untrusted"), t.slice(-300));
    await s.shot("04-untrusted-cert");
    check("«Назад» — к приветствию", (await s.click("Назад")) && (await s.until("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 5)));
    await s.click("Настройки");
    await s.until("!!document.querySelector('.tabs')", 5);
    for (const tab of ["general", "notify", "orgs", "network", "updates", "diag", "about"]) {
      await s.js(`document.querySelector('#tab-${tab}').click()`);
      await sleep(tab === "diag" ? 1500 : 400);
      check(`настройки до входа: «${tab}» открывается`, (await s.js("document.querySelector('.panel h2')?.textContent ?? ''")).length > 0);
      await s.shot(`05-settings-${tab}`);
      if (tab === "diag") {
        const diag = await s.text();
        check("диагностика: файлы программы в порядке, хранилище входа доступно", diag.includes("в порядке") && diag.includes("DPAPI"), diag.slice(0, 600));
      }
    }
    await s.js("window.pg.call('shell:settings', { theme: 'dark' })");
    await s.js("document.querySelector('#tab-general').click()");
    await sleep(600);
    check("тёмная тема применяется", await s.js("matchMedia('(prefers-color-scheme: dark)').matches"));
    await s.shot("06-settings-dark");
    await s.js("window.pg.call('shell:settings', { theme: 'system', closeAction: 'quit' })");
    s.close();
  } finally {
    app.kill();
    untrusted.close();
  }
}

async function update() {
  const feed = await serve(8443, feedDir);
  const app = launch();
  try {
    const s = await shell();
    await s.js("window.pg.call('shell:settings', { theme: 'dark', closeAction: 'quit' })");
    await s.click("Настройки");
    await s.until("!!document.querySelector('#tab-updates')", 5);
    await s.js("document.querySelector('#tab-updates').click()");
    await sleep(500);
    await s.click("Проверить обновления");
    const found = await s.until(`document.querySelector('main').innerText.includes('Доступна версия ${nextVersion}')`, 40);
    check(`проверка обновлений: найдена ${nextVersion} (подпись манифеста верна)`, found, (await s.text()).slice(0, 400));
    await s.shot("10-update-available");
    await s.click("Скачать обновление");
    const ready = await s.until("document.querySelector('main').innerText.includes('загружена и проверена')", 180);
    check("загрузка: размер, SHA-256 и подпись Authenticode установщика проверены", ready, (await s.text()).slice(0, 400));
    await s.shot("11-update-ready");
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
  if (mode === "firstrun") await firstrun();
  else if (mode === "update") await update();
  else throw new Error("режим: firstrun | update");
} catch (e) {
  check("сценарий выполнен без исключений", false, e?.stack ?? e);
}
console.log(failed ? `НЕ ПРОШЛИ: ${failed}` : "все проверки пройдены");
process.exit(failed ? 1 : 0);
