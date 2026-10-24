// Сборка клиента: esbuild собирает главный процесс и preload в CommonJS (preload в песочнице — один файл без require), оболочку — в один модуль для app://.
// Ключ проверки обновлений (открытый) и признак тестовой сборки подставляются при сборке из окружения CI.
import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync, readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const out = join(root, "dist");
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const test = process.argv.includes("--test");

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "shell", "fonts"), { recursive: true });

// пиктограммы оболочки — из набора веб-интерфейса (один источник): строки вида `  name: <path …/>,` → разметка SVG
const front = join(root, "..", "frontend");
const iconsSrc = readFileSync(join(front, "src/components/Icons.tsx"), "utf8");
const icons = {};
for (const m of iconsSrc.matchAll(/^  (\w+): (<.*>),$/gm)) icons[m[1]] = m[2].replace(/^<>|<\/>$/g, "");
if (Object.keys(icons).length < 40) throw new Error("не удалось прочитать пиктограммы из Icons.tsx");
writeFileSync(join(root, "src/shell/icons.gen.ts"), `// Создано build.mjs из frontend/src/components/Icons.tsx — не править вручную.\nexport const ICONS = ${JSON.stringify(icons, null, 0)} as const;\n`);

// значки приложения — из frontend/public (тот же знак, что у сайта)
mkdirSync(join(out, "assets"), { recursive: true });
for (const [from, to] of [["icon-512.png", "icon.png"], ["favicon.svg", "icon.svg"], ["favicon-16.png", "tray.png"], ["favicon-32.png", "tray-hi.png"], ["favicon-live-32.png", "tray-unread-hi.png"]]) {
  cpSync(join(front, "public", from), join(out, "assets", to));
}

const define = {
  __APP_VERSION__: JSON.stringify(pkg.version),
  __UPDATE_PUBKEY__: JSON.stringify(process.env.PEREGOVORKA_UPDATE_PUBKEY || ""),
  __UPDATE_FEED__: JSON.stringify(process.env.PEREGOVORKA_UPDATE_FEED || pkg.peregovorka?.updateFeed || ""),
  __BUILD_KIND__: JSON.stringify(process.env.PEREGOVORKA_BUILD_KIND || "test"),      // release — только подписанная сборка CI; иначе «тестовая сборка»
};
const common = { bundle: true, platform: "node", target: "node22", sourcemap: false, legalComments: "none", define, logLevel: "warning" };

await build({ ...common, entryPoints: [join(root, "src/main/main.ts")], outfile: join(out, "main.js"), format: "cjs", external: ["electron"] });
for (const p of ["shell", "org"]) {
  await build({ ...common, entryPoints: [join(root, `src/preload/${p}.ts`)], outfile: join(out, `preload-${p}.js`), format: "cjs", external: ["electron"] });
}
for (const page of ["app", "picker", "indicator"]) {
  await build({ ...common, platform: "browser", target: "chrome130", entryPoints: [join(root, `src/shell/${page}.ts`)], outfile: join(out, "shell", `${page}.js`), format: "esm",
    loader: { ".woff2": "file", ".svg": "dataurl" }, assetNames: "fonts/[name]" });
}
await build({ ...common, platform: "browser", entryPoints: [join(root, "src/shell/shell.css")], outfile: join(out, "shell", "shell.css"), loader: { ".woff2": "file" }, assetNames: "fonts/[name]" });
for (const f of readdirSync(join(root, "src/shell")).filter((f) => f.endsWith(".html"))) cpSync(join(root, "src/shell", f), join(out, "shell", f));
cpSync(join(out, "assets"), join(out, "shell", "assets"), { recursive: true });          // оболочка ссылается на assets/icon.svg относительно app://shell/

if (test) {
  const tests = readdirSync(join(root, "test")).filter((f) => f.endsWith(".test.ts"));
  for (const t of tests) await build({ ...common, entryPoints: [join(root, "test", t)], outfile: join(out, "test", t.replace(/\.ts$/, ".js")), format: "cjs", external: ["electron"] });
}
writeFileSync(join(out, "build.json"), JSON.stringify({ version: pkg.version, kind: JSON.parse(define.__BUILD_KIND__), built_at: new Date().toISOString() }));
