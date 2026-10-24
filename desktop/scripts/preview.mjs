// npm run preview [-- --win] [-- --no-shots]: собранная программа БЕЗ установщика (electron-builder --dir: asar, предохранители, значки — как в выпуске)
// и обязательные проверки на ней с пустым профилем: первый запуск, работа без сервера, снимки и вёрстка локальных экранов.
//   • в Windows: release/win-unpacked/Peregovorka.exe — запускается прямо из папки, без EXE/MSI;
//   • в Linux: release/linux-unpacked (те же файлы приложения и упаковка) + проверки; с --win дополнительно собирается win-unpacked
//     и архив release/Peregovorka-<версия>-win-unpacked.zip — распаковать на компьютере Windows и запустить Peregovorka.exe
//     (проверка путей Windows — там: npm run preview на Windows или CI-задача preview).
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./cdp.mjs";

const args = process.argv.slice(2);
const win = process.platform === "win32";
const run = (cmd, a, env = {}) => {
  const r = spawnSync(cmd, a, { cwd: ROOT, stdio: "inherit", shell: win, env: { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: "false", ...env } });
  if (r.status !== 0) { console.error(`\nне удалось: ${cmd} ${a.join(" ")}`); process.exit(r.status || 1); }
};
const version = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8")).version;
const builder = join(ROOT, "node_modules", ".bin", win ? "electron-builder.cmd" : "electron-builder");

run(process.execPath, ["build.mjs"]);
const native = win ? ["--win", "dir", "--x64"] : ["--linux", "dir", "--x64"];
run(builder, [...native, "--publish", "never"]);
const app = win ? join(ROOT, "release", "win-unpacked", "Peregovorka.exe") : join(ROOT, "release", "linux-unpacked", "peregovorka-desktop");
if (!existsSync(app)) { console.error(`нет собранной программы: ${app}`); process.exit(1); }

if (!win && args.includes("--win")) {
  // без Wine: ресурсы exe (значок, версия) не правятся — для проверки достаточно; выпуск собирается на Windows в CI
  run(builder, ["--win", "dir", "--x64", "--publish", "never", "-c.win.signAndEditExecutable=false"]);
  const zip = join(ROOT, "release", `Peregovorka-${version}-win-unpacked.zip`);
  rmSync(zip, { force: true });
  run("python3", ["-c", `import shutil; shutil.make_archive(${JSON.stringify(zip.slice(0, -4))}, "zip", ${JSON.stringify(join(ROOT, "release"))}, "win-unpacked")`]);
  console.log(`\nдля Windows: ${zip} — распаковать и запустить win-unpacked\\Peregovorka.exe`);
}

const checks = ["firstrun", "offline", ...(args.includes("--no-shots") ? [] : ["shots"])];
for (const c of checks) {
  console.log(`\n=== проверка «${c}» собранной программы ===`);
  run(process.execPath, [join("scripts", "check.mjs"), c, "--app", app, "--out", join(ROOT, "out", "preview")]);
}
console.log(`\nпредварительная сборка готова и проверена: ${app}\nснимки: ${join(ROOT, "out", "preview")}`);
