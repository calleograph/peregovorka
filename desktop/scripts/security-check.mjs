// Проверка безопасности исходников клиента (CI): механизмы защиты Electron/Chromium не отключены ни в одном месте, проверка TLS не ослаблена,
// у каждого окна — песочница и изоляция контекста, предохранители сборки включены. Код 1 — найдено нарушение.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const files = [];
const walk = (d) => { for (const f of readdirSync(d)) { const p = join(d, f); if (statSync(p).isDirectory()) walk(p); else if (/\.(ts|mjs|html)$/.test(f)) files.push(p); } };
walk(join(root, "src"));

const FORBIDDEN = [
  [/nodeIntegration\s*:\s*true/, "nodeIntegration: true"],
  [/nodeIntegrationInSubFrames\s*:\s*true/, "nodeIntegrationInSubFrames: true"],
  [/contextIsolation\s*:\s*false/, "contextIsolation: false"],
  [/sandbox\s*:\s*false/, "sandbox: false"],
  [/webSecurity\s*:\s*false/, "webSecurity: false"],
  [/allowRunningInsecureContent\s*:\s*true/, "allowRunningInsecureContent: true"],
  [/webviewTag\s*:\s*true/, "webviewTag: true"],
  [/experimentalFeatures\s*:\s*true/, "experimentalFeatures: true"],
  [/setCertificateVerifyProc/, "собственная проверка сертификатов"],
  [/ignore-certificate-errors|ignore-ssl-errors|allow-insecure-localhost/, "отключение проверки TLS ключом Chromium"],
  [/["']certificate-error["'][\s\S]{0,200}callback\(\s*true/, "принятие недоверенного сертификата"],
  [/NODE_TLS_REJECT_UNAUTHORIZED/, "отключение TLS в Node"],
  [/disable-web-security|disable-site-isolation/, "отключение защиты Chromium ключом"],
  [/\.innerHTML\s*=|insertAdjacentHTML|document\.write\(/, "вставка HTML строкой в оболочке"],
  [/eval\(|new Function\(/, "выполнение строки как кода"],
  [/shell\.openExternal\((?![^)]*(url|u|p\.linkURL)\))/, "openExternal без проверенной переменной"],
];
// единственное разрешённое исключение: чтение сертификата ТОЛЬКО для показа человеку после отказа Chromium (src/main/probe.ts, certDetails)
const ALLOW = [["src/main/probe.ts", "rejectUnauthorized: false"]];

let bad = 0;
for (const f of files) {
  const rel = relative(root, f).replaceAll("\\", "/");
  const src = readFileSync(f, "utf8");
  for (const [re, what] of FORBIDDEN) if (re.test(src)) { console.error(`✗ ${rel}: ${what}`); bad++; }
  if (/rejectUnauthorized\s*:\s*false/.test(src) && !ALLOW.some(([p]) => p === rel)) { console.error(`✗ ${rel}: rejectUnauthorized: false`); bad++; }
}
// каждое окно и представление: явные contextIsolation + sandbox + nodeIntegration:false в общих настройках
const orgs = readFileSync(join(root, "src/main/orgs.ts"), "utf8");
const win = readFileSync(join(root, "src/main/window.ts"), "utf8");
for (const [name, s] of [["orgs.ts webPreferences", orgs], ["window.ts shellPrefs", win]]) {
  for (const need of ["contextIsolation: true", "sandbox: true", "nodeIntegration: false"]) if (!s.includes(need)) { console.error(`✗ ${name}: нет ${need}`); bad++; }
}
for (const need of ["setPermissionRequestHandler", "setPermissionCheckHandler", "setWindowOpenHandler", "will-navigate", "setDisplayMediaRequestHandler", "setDevicePermissionHandler"]) {
  if (!orgs.includes(need)) { console.error(`✗ orgs.ts: нет ${need}`); bad++; }
}
const builder = readFileSync(join(root, "electron-builder.yml"), "utf8");
for (const need of ["runAsNode: false", "enableCookieEncryption: true", "enableNodeOptionsEnvironmentVariable: false", "enableNodeCliInspectArguments: false",
  "enableEmbeddedAsarIntegrityValidation: true", "onlyLoadAppFromAsar: true", "grantFileProtocolExtraPrivileges: false"]) {
  if (!builder.includes(need)) { console.error(`✗ electron-builder.yml: нет ${need}`); bad++; }
}
const main = readFileSync(join(root, "src/main/main.ts"), "utf8");
if (!/content-security-policy/.test(main) || /unsafe-eval|unsafe-inline/.test(main)) { console.error("✗ main.ts: CSP оболочки отсутствует или ослаблена"); bad++; }
console.log(bad ? `нарушений: ${bad}` : `безопасность исходников: ${files.length} файлов, нарушений нет`);
process.exit(bad ? 1 : 0);
