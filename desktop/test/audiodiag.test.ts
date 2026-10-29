import { test } from "node:test";
import assert from "node:assert/strict";
import { diagnoseMic, wasapiFindings } from "../src/common/media";
import { filetimeToIso, parseConsentStore } from "../src/main/audiolog";

test("коды WASAPI из журнала Chromium: занятость, запрет доступа и неизвестный код различаются", () => {
  const lines = [
    '[1:2:1029/101500.123:VERBOSE1:media_stream_manager.cc(1544)] WAIS[id=a1]: Open => (ERROR: IAudioClient::Initialize=[HRESULT: 0x8889000A, WCode: 0, message: "Устройство используется"])',
    '[1:2:1029/101501.000:VERBOSE1:media_stream_manager.cc(1544)] WAIS[id=a2]: Open => (ERROR: IAudioClient::Initialize=[HRESULT: 0x8889000A, WCode: 0, message: "x"])',
    '[1:2:1029/101502.000:VERBOSE1:media_stream_manager.cc(1544)] WAIS[id=a3]: Open => (ERROR: IAudioClient::Initialize=[HRESULT: 0x80070005, WCode: 0, message: "Отказано в доступе"])',
    '[1:2:1029/101503.000:VERBOSE1:media_stream_manager.cc(1544)] WAIS[id=a4]: Open => (ERROR: IAudioClient::Initialize=[HRESULT: 0x88890999, WCode: 0, message: "?"])',
    "посторонняя строка журнала с HRESULT: 0x8889000A, но не о захвате звука",
  ];
  const f = wasapiFindings(lines);
  assert.deepEqual(f.map((x) => [x.name, x.count]), [["AUDCLNT_E_DEVICE_IN_USE", 2], ["E_ACCESSDENIED", 1], ["HRESULT 0x88890999", 1]]);
  assert.match(f[0].meaning, /монопольн/);
});

test("журнал доступа Windows к микрофону: время последнего использования приложения и Edge", () => {
  const out = [
    "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone\\NonPackaged\\C:#Users#u#AppData#Local#Programs#Peregovorka#Peregovorka.exe",
    "    LastUsedTimeStart    REG_QWORD    0x1dc48a6f1b2c000",
    "    LastUsedTimeStop    REG_QWORD    0x0",
    "",
    "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone\\NonPackaged\\C:#Program Files (x86)#Microsoft#Edge#Application#msedge.exe",
    "    LastUsedTimeStart    REG_QWORD    0x1dc48a6f1b2c000",
    "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone\\NonPackaged\\C:#Tools#other.exe",
    "    LastUsedTimeStart    REG_QWORD    0x1dc48a6f1b2c000",
  ].join("\r\n");
  const r = parseConsentStore(out, /peregovorka|msedge\.exe/i);
  assert.deepEqual(r.map(({ app }) => app), ["Peregovorka.exe", "msedge.exe"]);
  assert.equal(r[0].lastStop, null, "0 — использования не было (или идёт сейчас)");
  assert.match(r[0].lastStart!, /^20\d\d-/);
  assert.equal(filetimeToIso("0x0"), null);
  assert.equal(filetimeToIso("0x19DB1DED53E8000"), "1970-01-01T00:00:00.000Z");
});

test("микрофон: «Could not start audio source» — не занятость; с Kaspersky — шаги для его политики", () => {
  const base = { platform: "win32", osAccess: "granted", micCount: 1, selectedMissing: false };
  const start = diagnoseMic({ ...base, error: { name: "NotReadableError", message: "Could not start audio source" }, securityProducts: ["Kaspersky Endpoint Security для Windows"] });
  assert.equal(start.cause, "start_failed");
  assert.doesNotMatch(start.title, /занят/);
  assert.ok(start.steps.some((s) => /Предотвращение вторжений/.test(s) && /Доверенные/.test(s)));
  const busy = diagnoseMic({ ...base, error: { name: "NotReadableError", message: "Device in use" } });
  assert.equal(busy.cause, "busy");
  assert.equal(busy.reboot, true);
  const removed = diagnoseMic({ ...base, error: { name: "NotReadableError", message: "Device was removed" } });
  assert.equal(removed.cause, "removed");
  const sys = diagnoseMic({ ...base, error: { name: "NotAllowedError", message: "Permission denied by system" } });
  assert.equal(sys.cause, "security_product");
  assert.match(sys.text, /E_ACCESSDENIED/);
});
