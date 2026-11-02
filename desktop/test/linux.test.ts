import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { autostartEntry, waylandSession } from "../src/common/linux";
import { osText } from "../src/common/osText";
import { diagnoseMic } from "../src/common/media";
import { installedVersion } from "../src/main/linuxPackage";
import { loadPolicy } from "../src/main/settings";
import { relaunchCommand } from "../src/main/updateCore";

test("слова о системе: Windows — прежние, Linux — без упоминаний Windows, DPAPI и MSI", () => {
  const w = osText("win32"), l = osText("linux");
  assert.equal(w.product, "Peregovorka для Windows");
  assert.equal(w.trustOk, "защищено Windows (DPAPI)");
  assert.equal(w.managedUpdates, "Версию приложения устанавливает администратор (корпоративная установка MSI)");
  assert.equal(l.product, "Peregovorka для Linux");
  for (const v of Object.values(l)) assert.doesNotMatch(v, /Windows|DPAPI|MSI|реестр/, v);
  assert.match(l.managedUpdates, /apt update && sudo apt upgrade/);
});

test("Linux: подсказки по микрофону без монопольного режима и параметров Windows", () => {
  const base = { platform: "linux", osAccess: "unknown", micCount: 1, selectedMissing: false };
  for (const name of ["NotAllowedError", "NotFoundError", "NotReadableError", "AbortError", "Странная"]) {
    const r = diagnoseMic({ ...base, micCount: name === "NotFoundError" ? 0 : 1, error: { name, message: name === "NotReadableError" ? "Device in use" : "" } });
    assert.notEqual(r.cause, "none");
    assert.deepEqual(r.actions, ["retry"]);                         // кнопок открытия параметров Windows нет
    for (const t of [r.title, r.text, ...r.steps]) assert.doesNotMatch(t, /Windows|монопольн|WASAPI/, t);
  }
  assert.equal(diagnoseMic({ ...base, micCount: 0, error: { name: "NotFoundError" } }).cause, "no_device");
  assert.match(diagnoseMic({ ...base, error: { name: "NotReadableError", message: "Device in use" } }).steps.join(" "), /pipewire/);
});

test("автозапуск XDG: «в трей» — с --hidden, «не запускать» — файла нет", () => {
  assert.equal(autostartEntry("off"), null);
  const tray = autostartEntry("tray")!, win = autostartEntry("window")!;
  assert.match(tray, /^\[Desktop Entry\]\nType=Application\n/);
  assert.match(tray, /\nExec=peregovorka --hidden\n/);
  assert.match(win, /\nExec=peregovorka\n/);
  assert.match(win, /\nIcon=peregovorka\n/);
});

test("Wayland: только Linux и сеанс Wayland; принудительный X11 — свой выбор экрана", () => {
  assert.equal(waylandSession({ XDG_SESSION_TYPE: "wayland" }, "linux"), true);
  assert.equal(waylandSession({ WAYLAND_DISPLAY: "wayland-0" }, "linux"), true);
  assert.equal(waylandSession({ XDG_SESSION_TYPE: "x11" }, "linux"), false);
  assert.equal(waylandSession({ XDG_SESSION_TYPE: "wayland", ELECTRON_OZONE_PLATFORM_HINT: "x11" }, "linux"), false);
  assert.equal(waylandSession({ XDG_SESSION_TYPE: "wayland" }, "win32"), false);
});

test("версия установленного пакета: из resources/package-version, мусор и отсутствие файла — null", () => {
  const d = mkdtempSync(join(tmpdir(), "pg-pkg-"));
  assert.equal(installedVersion(d), null);
  writeFileSync(join(d, "package-version"), "1.5.1\n");
  assert.equal(installedVersion(d), "1.5.1");
  writeFileSync(join(d, "package-version"), "1.5.1; rm -rf /");
  assert.equal(installedVersion(d), null);
});

test("политики Linux: установленная программа не берёт файл из переменной окружения пользователя", async () => {
  if (process.platform !== "linux") return;
  const d = mkdtempSync(join(tmpdir(), "pg-pol-"));
  const f = join(d, "policy.json");
  writeFileSync(f, JSON.stringify({ AllowAddServers: 0 }));
  process.env.PEREGOVORKA_POLICY_FILE = f;
  try {
    assert.equal((await loadPolicy(false)).allowAddServers, false);        // запуск из исходников (испытания) — файл из переменной
    assert.notEqual((await loadPolicy(true)).allowAddServers, false);      // установленная — только /etc/peregovorka/policy.json
  } finally { delete process.env.PEREGOVORKA_POLICY_FILE; }
});

test("перезапуск после apt upgrade: путь программы без «(deleted)», без --hidden", () => {
  assert.deepEqual(relaunchCommand("/opt/Peregovorka/peregovorka (deleted)", ["/opt/Peregovorka/peregovorka", "--hidden", "peregovorka://x"]),
    ["/opt/Peregovorka/peregovorka", "peregovorka://x"]);
  assert.deepEqual(relaunchCommand("/opt/Peregovorka/peregovorka", ["/opt/Peregovorka/peregovorka"]), ["/opt/Peregovorka/peregovorka"]);
});
