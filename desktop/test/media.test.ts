import { test } from "node:test";
import assert from "node:assert/strict";
import { cleanMedia, DEFAULT_MEDIA, deviceKind, diagnoseMic, diffDevices, findByLabel, kindAdvice, levelOf, REBOOT_HINT, soundCommand, SOUND_SETTINGS, type MicProblemInput } from "../src/common/media";
import { loadSettings, SettingsStore } from "../src/main/settings";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("настройки звука: неизвестные поля отбрасываются, значения проверяются и укорачиваются", () => {
  assert.deepEqual(cleanMedia(undefined), DEFAULT_MEDIA);
  const m = cleanMedia({ mic: "  Гарнитура USB\n", speaker: 5, camera: "x".repeat(500), noise: "turbo", echo: "yes", agc: false, evil: 1 });
  assert.equal(m.mic, "Гарнитура USB");
  assert.equal(m.speaker, "", "не строка — значение по умолчанию");
  assert.equal(m.camera.length, 200);
  assert.equal(m.noise, "enhanced", "неизвестный режим — прежний");
  assert.equal(m.echo, true);
  assert.equal(m.agc, false);
  assert.ok(!("evil" in m));
  assert.equal(cleanMedia({ noise: "standard" }, { ...DEFAULT_MEDIA, mic: "A" }).mic, "A", "частичная правка не стирает остальное");
});

test("настройки звука сохраняются в профиле и читаются заново; старый файл без раздела получает значения по умолчанию", () => {
  const dir = mkdtempSync(join(tmpdir(), "pg-media-"));
  const st = new SettingsStore(dir);
  assert.deepEqual(st.get().media, DEFAULT_MEDIA);
  st.patch({ media: { mic: "Микрофон (USB)", noise: "off" } });
  assert.equal(JSON.parse(readFileSync(join(dir, "settings.json"), "utf8")).media.mic, "Микрофон (USB)");
  assert.equal(new SettingsStore(dir).get().media.noise, "off");
  assert.deepEqual(loadSettings({ closeAction: "quit" }, false).media, DEFAULT_MEDIA);
  st.patch({ media: { mic: 42 } });
  assert.equal(st.get().media.mic, "Микрофон (USB)", "некорректное значение не затирает сохранённое");
});

test("вид устройства определяется по названию как подсказка", () => {
  assert.equal(deviceKind("Головной телефон (Bluetooth Hands-Free Audio)"), "bluetooth");
  assert.equal(deviceKind("AirPods Pro"), "bluetooth");
  assert.equal(deviceKind("USB PnP Sound Device"), "usb");
  assert.equal(deviceKind("Microphone Array (Realtek High Definition Audio)"), "builtin");
  assert.equal(deviceKind("Микрофон (встроенный)"), "builtin");
  assert.equal(deviceKind("CABLE Output (VB-Audio Virtual Cable)"), "virtual");
  assert.equal(deviceKind("Jabra Evolve"), "other");
  assert.match(kindAdvice("bluetooth", "mic") ?? "", /Hands-Free/);
  assert.equal(kindAdvice("bluetooth", "speaker"), null);
  assert.equal(kindAdvice("usb", "mic"), null);
});

test("подключение и отключение устройств: по названиям и по количеству, пока названий нет", () => {
  const a = [{ kind: "audioinput" as const, label: "Встроенный микрофон" }, { kind: "videoinput" as const, label: "Камера ноутбука" }];
  const b = [...a, { kind: "audioinput" as const, label: "Гарнитура USB" }];
  assert.deepEqual(diffDevices(a, b), { added: ["Микрофон подключён: Гарнитура USB"], removed: [] });
  assert.deepEqual(diffDevices(b, a), { added: [], removed: ["Микрофон отключён: Гарнитура USB"] });
  assert.deepEqual(diffDevices(a, a), { added: [], removed: [] });
  const anon = [{ kind: "audioinput" as const, label: "" }];
  assert.deepEqual(diffDevices(anon, [...anon, ...anon]).added, ["Микрофон: подключено новое устройство"]);
  assert.deepEqual(diffDevices(anon, []).removed, ["Микрофон: устройство отключено"]);
});

test("поиск устройства по сохранённому названию", () => {
  const list = [{ label: "A", id: 1 }, { label: "B", id: 2 }];
  assert.equal(findByLabel(list, "B")?.id, 2);
  assert.equal(findByLabel(list, ""), null, "пусто — по умолчанию");
  assert.equal(findByLabel(list, "C"), null, "устройство не найдено — по умолчанию, а не ошибка");
});

const base: MicProblemInput = { platform: "win32", error: null, osAccess: "granted", micCount: 2, selectedMissing: false };
const diag = (over: Partial<MicProblemInput>) => diagnoseMic({ ...base, ...over });

test("микрофон: нет ошибки — нет проблемы", () => {
  assert.equal(diag({}).cause, "none");
});

test("микрофон: запрет Windows определяется по признаку системы и ведёт в параметры конфиденциальности", () => {
  const p = diag({ error: { name: "NotAllowedError" }, osAccess: "denied" });
  assert.equal(p.cause, "os_privacy");
  assert.equal(p.confidence, "certain");
  assert.ok(p.actions.includes("open_privacy_mic"));
  assert.equal(p.reboot, false);
  const q = diag({ error: { name: "NotAllowedError" }, osAccess: "granted" });
  assert.equal(q.cause, "denied");
  assert.equal(q.confidence, "possible", "система не запрещает — причина не доказана");
});

test("микрофон: «занят» не выдаётся за доказанный монопольный режим; рекомендация о перезагрузке — с оговоркой", () => {
  const p = diag({ error: { name: "NotReadableError" } });
  assert.equal(p.cause, "busy");
  assert.equal(p.confidence, "possible");
  assert.match(p.text, /не удалось открыть|точную причину установить нельзя/);
  assert.doesNotMatch(p.title, /монопольн/i, "заголовок не утверждает монопольный режим");
  assert.ok(p.steps.some((s) => /монопольном режиме/.test(s)));
  assert.ok(p.actions.includes("open_mic_settings") && p.actions.includes("retry"));
  assert.equal(p.reboot, true);
  assert.match(REBOOT_HINT, /рекомендуется перезагрузить компьютер, чтобы изменения корректно применились и оборудование повторно инициализировалось/);
  assert.match(REBOOT_HINT, /Часто достаточно/, "перезагрузка не названа обязательной во всех случаях");
});

test("микрофон: нет устройств, выбранное устройство исчезло, неподдерживаемые параметры", () => {
  assert.equal(diag({ error: { name: "NotFoundError" }, micCount: 0 }).cause, "no_device");
  assert.ok(diag({ error: { name: "NotFoundError" }, micCount: 0 }).actions.includes("open_mic_settings"));
  const gone = diag({ error: { name: "OverconstrainedError" }, selectedMissing: true });
  assert.equal(gone.cause, "selected_missing");
  assert.equal(gone.confidence, "certain");
  assert.equal(diag({ error: { name: "OverconstrainedError" } }).cause, "constraints");
  assert.equal(diag({ error: { name: "Странное" } }).cause, "unknown");
});

test("микрофон: вне Windows нет ссылок на настройки Windows", () => {
  const p = diag({ platform: "linux", error: { name: "NotReadableError" } });
  assert.deepEqual(p.actions, ["retry"]);
});

test("системные настройки: только фиксированные команды, без параметров от страницы", () => {
  assert.deepEqual(soundCommand("mic-classic"), { exe: "rundll32.exe", args: ["shell32.dll,Control_RunDLL", "mmsys.cpl,,1"], fallback: "ms-settings:sound" });
  assert.equal(soundCommand("privacy-mic").uri, "ms-settings:privacy-microphone");
  assert.equal(soundCommand("privacy-cam").uri, "ms-settings:privacy-webcam");
  assert.equal(soundCommand("sound").uri, "ms-settings:sound");
  assert.deepEqual([...SOUND_SETTINGS].sort(), ["mic-classic", "privacy-cam", "privacy-mic", "sound"]);
  for (const k of SOUND_SETTINGS) assert.ok(!/[;&|<>`$]/.test(JSON.stringify(soundCommand(k))), "в командах нет управляющих символов оболочки");
});

test("уровень сигнала: тишина — 0, полная амплитуда — 1, шкала по децибелам", () => {
  assert.equal(levelOf(new Float32Array(512)), 0);
  assert.ok(Math.abs(levelOf(new Float32Array(512).fill(1)) - 1) < 1e-9);
  const half = levelOf(new Float32Array(512).fill(0.001));       // –60 дБ — нижняя граница
  assert.ok(half < 0.01);
  const mid = levelOf(new Float32Array(512).fill(0.03));          // около –30 дБ
  assert.ok(mid > 0.4 && mid < 0.6, String(mid));
});
