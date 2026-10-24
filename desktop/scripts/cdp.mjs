// Запуск приложения и управление его окнами через CDP (--remote-debugging-port) — общий для `npm run dev`, `npm run preview` и CI.
// Playwright (_electron) не подходит: ему нужен --inspect главного процесса, а в собранной программе он закрыт предохранителем (fuses) —
// и правильно; CDP окон работает и с собранной программой, и с запуском из исходников.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Команда запуска: "dev" — Electron из node_modules с исходниками (dist/), иначе путь к собранной программе (win-unpacked\Peregovorka.exe, linux-unpacked/peregovorka-desktop). */
export function appCommand(app) {
  if (app === "dev") {
    const bin = join(ROOT, "node_modules", ".bin", process.platform === "win32" ? "electron.cmd" : "electron");
    return { cmd: bin, args: [ROOT], shell: process.platform === "win32" };
  }
  if (!existsSync(app)) throw new Error(`нет программы: ${app}`);
  return { cmd: app, args: [], shell: false };
}

/** Запуск с отдельным профилем (--user-data-dir: настройки, организации, журналы) и портом CDP. */
export function launch(app, { port = 9333, profile, extra = [], stdio = "ignore" } = {}) {
  const { cmd, args, shell } = appCommand(app);
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;                                           // иначе Electron стартует как обычный Node
  const all = [...args, `--remote-debugging-port=${port}`, "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", ...extra];
  if (profile) all.push(`--user-data-dir=${profile}`);
  if (process.platform === "linux") all.push("--no-sandbox");               // проверки под Xvfb без SUID-песочницы; в Windows песочница не трогается
  return spawn(cmd, all, { stdio, env, shell });
}

/** Ждать вкладку окна по началу адреса (по умолчанию — оболочка приложения). */
export async function page(port = 9333, prefix = "app://shell/index.html", sec = 60) {
  for (let i = 0; i < sec * 2; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const t = list.find((x) => x.url.startsWith(prefix));
      if (t) return connect(t.webSocketDebuggerUrl);
    } catch { /* ещё запускается */ }
    await sleep(500);
  }
  throw new Error(`окно ${prefix} не появилось за ${sec} с`);
}

export async function pages(port, prefix = "app://shell/") {
  try { return (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).filter((x) => x.url.startsWith(prefix)); } catch { return []; }
}

export async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0;
  const wait = new Map();
  const events = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } else if (d.method) events.push(d);
  };
  // каждый вызов — с пределом времени: зависший вызов (например, снимок невидимого окна) не вешает проверку
  const send = (method, params = {}, ms = 20000) => new Promise((r, j) => {
    const n = ++id;
    const t = setTimeout(() => { wait.delete(n); j(new Error(`CDP ${method}: нет ответа за ${ms / 1000} с`)); }, ms);
    wait.set(n, (d) => { clearTimeout(t); r(d); });
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
  return {
    send, js, events,
    close: () => ws.close(),
    async until(expr, sec = 15) { for (let i = 0; i < sec * 4; i++) { if (await js(expr).catch(() => false)) return true; await sleep(250); } return false; },
    async shot(file) {
      try { const r = await send("Page.captureScreenshot", { format: "png" }, 10000); if (r.result?.data) { (await import("node:fs")).writeFileSync(file, Buffer.from(r.result.data, "base64")); return true; } }
      catch (e) { console.log(`снимок ${file} не получен: ${e.message}`); }
      return false;
    },
    click: (text) => js(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().includes(${JSON.stringify(text)}) && !b.disabled); if (b) b.click(); return !!b; })()`),
    text: () => js("document.querySelector('main')?.innerText ?? ''"),
    /** Ошибки страницы с момента подключения: исключения, console.error, ошибки загрузки ресурсов. */
    async watchErrors() { await send("Runtime.enable"); await send("Log.enable"); },
    errors() {
      return events.flatMap((e) => {
        if (e.method === "Runtime.exceptionThrown") return [`исключение: ${e.params.exceptionDetails?.exception?.description ?? e.params.exceptionDetails?.text}`];
        if (e.method === "Runtime.consoleAPICalled" && e.params.type === "error") return [`console.error: ${e.params.args.map((a) => a.value ?? a.description).join(" ")}`];
        if (e.method === "Log.entryAdded" && e.params.entry.level === "error") return [`${e.params.entry.source}: ${e.params.entry.text} ${e.params.entry.url ?? ""}`];
        return [];
      });
    },
  };
}
