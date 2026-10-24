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
