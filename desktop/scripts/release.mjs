// npm run release -- X.Y.Z [--push]: выпуск клиента Windows. Сначала всё, что можно проверить локально (типы, модульные тесты, безопасность исходников,
// предварительная сборка с проверками первого запуска, работы без сервера и вёрстки), потом версия в package.json, коммит и тег desktop-vX.Y.Z.
// С --push тег отправляется: CI собирает EXE/MSI на Windows, повторяет предварительные проверки там, подписывает (если настроено),
// испытывает установку/обновление/удаление и публикует выпуск. Совместимый выпуск сервера — отдельно (./scripts/release.sh, полный CI).
import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./cdp.mjs";

const [version] = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const push = process.argv.includes("--push");
const die = (m) => { console.error(`выпуск остановлен: ${m}`); process.exit(1); };
const git = (...a) => execFileSync("git", a, { cwd: ROOT, encoding: "utf8" }).trim();
const step = (title, cmd, a) => {
  console.log(`\n=== ${title} ===`);
  const r = spawnSync(cmd, a, { cwd: ROOT, stdio: "inherit", shell: process.platform === "win32" });
  if (r.status !== 0) die(`${title} — не пройдено`);
};

if (!/^\d+\.\d+\.\d+$/.test(version || "")) die("укажите версию: npm run release -- X.Y.Z");
if (git("status", "--porcelain")) die("есть незакоммиченные изменения");
if (git("rev-parse", "--abbrev-ref", "HEAD") !== "main") die("выпуск — только с ветки main");
if (git("tag", "--list", `desktop-v${version}`)) die(`тег desktop-v${version} уже есть`);
const changelog = readFileSync(join(ROOT, "CHANGELOG.md"), "utf8");
if (!new RegExp(`^## ${version.replace(/\./g, "\\.")}( |$)`, "m").test(changelog)) die(`в desktop/CHANGELOG.md нет раздела «## ${version}» (для пользователя: что нового)`);

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
step("типы", npm, ["run", "typecheck"]);
step("модульные тесты", npm, ["test"]);
step("безопасность исходников", process.execPath, ["scripts/security-check.mjs"]);
step("предварительная сборка и проверки собранной программы", process.execPath, ["scripts/preview.mjs"]);

// Витрина репозитория (README): версия и ссылки на установщики текущего выпуска — всегда новые, без ручного шага.
const readme = join(ROOT, "..", "README.md");
const repo = "https://github.com/calleograph/peregovorka", tag = `desktop-v${version}`, rel = `${repo}/releases/download/${tag}`;
const block = [
  `**Версия ${version}** · [страница выпуска](${repo}/releases/tag/${tag}) · [контрольные суммы](${rel}/SHA256SUMS.txt)`, "",
  `- **[Peregovorka-Setup-${version}.exe](${rel}/Peregovorka-Setup-${version}.exe)** — установка для себя, без прав администратора`,
  `- **[Peregovorka-${version}-x64.msi](${rel}/Peregovorka-${version}-x64.msi)** — для развёртывания в организации (групповые политики)`, "",
].join("\n");
const rm = readFileSync(readme, "utf8");
if (!rm.includes("<!-- desktop-release:start -->")) die("в README.md нет блока <!-- desktop-release -->");
writeFileSync(readme, rm.replace(/<!-- desktop-release:start -->\n[\s\S]*?(?=\n[^\n]*\n<!-- desktop-release:end -->)/, `<!-- desktop-release:start -->\n${block}`));
git("add", "../README.md");
// Ссылка на установщик в подвале сайта (frontend/src/desktopDist.ts) — тоже всегда на текущий выпуск.
const dist = join(ROOT, "..", "frontend", "src", "desktopDist.ts");
const ds = readFileSync(dist, "utf8");
if (!/export const DESKTOP_VERSION = "[\d.]+";/.test(ds)) die("в frontend/src/desktopDist.ts нет строки DESKTOP_VERSION");
writeFileSync(dist, ds.replace(/export const DESKTOP_VERSION = "[\d.]+";/, `export const DESKTOP_VERSION = "${version}";`));
git("add", "../frontend/src/desktopDist.ts");

for (const f of ["package.json", "package-lock.json"]) {
  const p = join(ROOT, f);
  const d = JSON.parse(readFileSync(p, "utf8"));
  d.version = version;
  if (d.packages?.[""]) d.packages[""].version = version;
  writeFileSync(p, JSON.stringify(d, null, 2) + "\n");
}
git("add", "package.json", "package-lock.json");
git("commit", "-m", `Peregovorka для Windows ${version}`);
git("tag", "-a", `desktop-v${version}`, "-m", `Peregovorka для Windows ${version}`);
console.log(`\nкоммит и тег desktop-v${version} созданы`);
if (push) {
  git("push", "origin", "HEAD", `desktop-v${version}`);
  console.log("отправлено: CI соберёт EXE/MSI, проверит на Windows и опубликует выпуск");
} else console.log(`отправить: git push origin HEAD desktop-v${version}   (или повторить с --push)`);
