// npm run dev [-- --fresh]: Electron из исходников с журналом запуска в терминале (--debug-log: главный процесс, preload, консоль оболочки).
// Правка src/shell или общих токенов/значков веб-интерфейса → сборка (доли секунды) и перезагрузка окна оболочки; правка главного процесса,
// preload или src/common → перезапуск Electron. Установщики не собираются. --fresh — пустой профиль (каждый запуск dev — как первый запуск у пользователя),
// иначе профиль разработчика desktop/.dev-profile (организации и вход сохраняются между перезапусками).
import { spawnSync } from "node:child_process";
import { mkdtempSync, watch } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { launch, pages, connect, ROOT, sleep } from "./cdp.mjs";

const PORT = 9339;
const fresh = process.argv.includes("--fresh");
const profile = fresh ? mkdtempSync(join(tmpdir(), "pg-dev-")) : join(ROOT, ".dev-profile");
const extra = ["--debug-log", ...process.argv.slice(2).filter((a) => a !== "--fresh")];
const t = () => new Date().toTimeString().slice(0, 8);

function build() {
  const t0 = Date.now();
  const r = spawnSync(process.execPath, [join(ROOT, "build.mjs")], { stdio: "inherit" });
  if (r.status === 0) console.log(`[dev ${t()}] собрано за ${Date.now() - t0} мс`);
  return r.status === 0;
}

let app = null;
let restarting = false;
function start() {
  app = launch("dev", { port: PORT, profile, extra, stdio: "inherit" });
  app.on("exit", (code) => { if (!restarting) { console.log(`[dev ${t()}] приложение закрыто (${code ?? "сигнал"})`); process.exit(0); } });
  console.log(`[dev ${t()}] запущено; профиль: ${profile}; CDP: http://127.0.0.1:${PORT}`);
}
async function restart() {
  restarting = true;
  await new Promise((r) => { app.once("exit", r); app.kill(); });
  restarting = false;
  start();
}
async function reloadShell() {
  for (const p of await pages(PORT)) {
    try { const c = await connect(p.webSocketDebuggerUrl); await c.send("Page.reload", { ignoreCache: true }); c.close(); } catch { /* окно закрывается */ }
  }
  console.log(`[dev ${t()}] окно оболочки перезагружено`);
}

if (!build()) process.exit(1);
start();

let timer = null;
const changed = new Set();
const onChange = (file) => {
  if (!file || /icons\.gen\.ts$|\.(swp|tmp)$|~$/.test(file)) return;          // icons.gen.ts пишет сама сборка
  changed.add(file);
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const files = [...changed];
    changed.clear();
    console.log(`[dev ${t()}] изменено: ${files.map((f) => relative(ROOT, f)).join(", ")}`);
    if (!build()) return;                                                        // ошибка сборки — в терминале, окно не трогаем
    const shellOnly = files.every((f) => f.includes(join("src", "shell")) || f.includes("frontend"));
    if (shellOnly) await reloadShell(); else await restart();
  }, 200);
};
watch(join(ROOT, "src"), { recursive: true }, (_e, f) => onChange(f && join(ROOT, "src", f)));
for (const f of ["src/tokens.css", "src/components/Icons.tsx"]) watch(join(ROOT, "..", "frontend", f), () => onChange(join(ROOT, "..", "frontend", f)));
process.on("SIGINT", () => { restarting = true; app?.kill(); sleep(200).then(() => process.exit(0)); });
console.log(`[dev ${t()}] слежу за src/ и frontend/src/tokens.css, Icons.tsx — Ctrl+C для выхода`);
