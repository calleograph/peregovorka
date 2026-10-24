// Манифест обновлений приложения и его подпись ключом издателя (Ed25519). Приложение устанавливает обновление, только если подпись сходится
// с открытым ключом, встроенным в сборку (PEREGOVORKA_UPDATE_PUBKEY), а SHA-256 пакета — с манифестом.
//
//   node scripts/update-manifest.mjs keygen
//       новая пара ключей: закрытый — в секрет CI DESKTOP_UPDATE_KEY (PEM), открытый — в переменную CI DESKTOP_UPDATE_PUBKEY (base64). Закрытый ключ в репозиторий не кладётся.
//   node scripts/update-manifest.mjs make <версия> <установщик.exe> <адрес загрузки> [заметки.md] [страница выпуска] > update.json
//   node scripts/update-manifest.mjs sign update.json        → update.json.sig (ключ — DESKTOP_UPDATE_KEY или файл DESKTOP_UPDATE_KEY_FILE)
//   node scripts/update-manifest.mjs verify update.json      → проверка подписи (ключ — из DESKTOP_UPDATE_PUBKEY)
import { createHash, createPrivateKey, createPublicKey, generateKeyPairSync, sign, verify } from "node:crypto";
import { readFileSync, writeFileSync, statSync } from "node:fs";
import { basename } from "node:path";

const [cmd, ...args] = process.argv.slice(2);
const die = (m) => { console.error(m); process.exit(1); };
const pubFromB64 = (b64) => createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(b64, "base64")]), format: "der", type: "spki" });

if (cmd === "keygen") {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  console.log("DESKTOP_UPDATE_PUBKEY=" + publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("base64"));
  console.log("DESKTOP_UPDATE_KEY (закрытый, только в секреты CI):\n" + privateKey.export({ format: "pem", type: "pkcs8" }));
} else if (cmd === "make") {
  const [version, file, url, notes, page] = args;
  if (!/^\d+\.\d+\.\d+$/.test(version || "") || !file || !/^https:\/\//.test(url || "") || (page && !/^https:\/\/\S+$/.test(page))) die("make <версия X.Y.Z> <установщик.exe> <https-адрес загрузки> [заметки] [https-страница выпуска]");
  const buf = readFileSync(file);
  const m = { product: "peregovorka-desktop", version, released: new Date().toISOString(), notes: notes ? readFileSync(notes, "utf8").slice(0, 4000) : "", ...(page ? { page } : {}),
    files: [{ kind: "nsis", name: basename(file), url, sha256: createHash("sha256").update(buf).digest("hex"), size: statSync(file).size }] };
  process.stdout.write(JSON.stringify(m, null, 1) + "\n");
} else if (cmd === "sign") {
  const key = process.env.DESKTOP_UPDATE_KEY || (process.env.DESKTOP_UPDATE_KEY_FILE ? readFileSync(process.env.DESKTOP_UPDATE_KEY_FILE, "utf8") : "");
  if (!key) die("нет DESKTOP_UPDATE_KEY (или DESKTOP_UPDATE_KEY_FILE — файл PEM с правами только для владельца)");
  const body = readFileSync(args[0]);
  writeFileSync(args[0] + ".sig", sign(null, body, createPrivateKey(key)).toString("base64") + "\n");
} else if (cmd === "verify") {
  const pub = process.env.DESKTOP_UPDATE_PUBKEY;
  if (!pub) die("нет DESKTOP_UPDATE_PUBKEY");
  const ok = verify(null, readFileSync(args[0]), pubFromB64(pub), Buffer.from(readFileSync(args[0] + ".sig", "utf8").trim(), "base64"));
  console.log(ok ? "подпись верна" : "ПОДПИСЬ НЕВЕРНА");
  process.exit(ok ? 0 : 1);
} else {
  die("команды: keygen | make | sign | verify");
}
