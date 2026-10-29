// Журнал звука Chromium и сведения Windows для разбора отказов микрофона. Только чтение, без записи звука.
//
// Chromium пишет ход открытия микрофона (WASAPI) в собственный журнал: WASAPIAudioInputStream::SendLogMessage → AudioLog::OnLogMessage →
// MediaStreamManager::SendMessageToNativeLog → VLOG(1). Строка отказа содержит настоящий код Windows:
//   «WAIS[id=…]: Open => (ERROR: IAudioClient::Initialize=[HRESULT: 0x8889000A, WCode: 0, message: "…"])».
// Штатные ключи Electron --enable-logging=file, --log-file и --vmodule=media_stream_manager=1 включают запись только этого модуля (немного
// строк на каждое открытие микрофона или камеры) в отдельный файл в каталоге журналов приложения; без этого кода ошибки «Микрофон не открылся»
// нельзя отличить монопольную блокировку от запрета защитной программы.
import type { App } from "electron";
import { execFile } from "node:child_process";
import { closeSync, mkdirSync, openSync, readSync, renameSync, statSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { wasapiFindings, type WasapiFinding } from "../common/media";
import { redact } from "../common/redact";

const MAX_BYTES = 4 * 1024 * 1024;

export const audioLogPath = (app: App) => join(app.getPath("userData"), "logs", "chromium-audio.log");

/** До app.whenReady(): журнал Chromium в файл; файл больше 4 МБ уходит в .old (одна прежняя копия). */
export function setupNativeAudioLog(app: App): void {
  const p = audioLogPath(app);
  try {
    mkdirSync(dirname(p), { recursive: true });
    if (statSync(p).size > MAX_BYTES) renameSync(p, `${p}.old`);
  } catch { /* файла ещё нет */ }
  app.commandLine.appendSwitch("enable-logging", "file");
  app.commandLine.appendSwitch("log-file", p);
  app.commandLine.appendSwitch("vmodule", "media_stream_manager=1");
}

function tail(file: string, bytes = 512 * 1024): string[] {
  try {
    const size = statSync(file).size, start = Math.max(0, size - bytes), len = size - start;
    const fd = openSync(file, "r");
    try { const b = Buffer.alloc(len); readSync(fd, b, 0, len, start); return b.toString("utf8").split(/\r?\n/); } finally { closeSync(fd); }
  } catch { return []; }
}

const run = (exe: string, args: string[], timeout = 8000) => new Promise<string>((res) => {
  execFile(exe, args, { timeout, windowsHide: true, maxBuffer: 1024 * 1024 }, (err, out) => res(err ? "" : String(out)));
});

/** Защитные программы, зарегистрированные в Центре безопасности Windows (только названия). */
export async function securityProducts(): Promise<string[]> {
  if (process.platform !== "win32") return [];
  const out = await run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
    "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object { $_.displayName }"]);
  return [...new Set(out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))].slice(0, 8);
}

export interface MicUse { app: string; lastStart: string | null; lastStop: string | null }

/** FILETIME (100-нс интервалы с 1601-01-01) → ISO; 0 — «не было». */
export function filetimeToIso(v: string): string | null {
  const n = BigInt(v.startsWith("0x") ? v : `0x${v}`);
  if (n === 0n) return null;
  return new Date(Number(n / 10000n - 11644473600000n)).toISOString();
}

/** Разбор `reg query …\ConsentStore\microphone\NonPackaged /s`: когда Windows в последний раз отдавала звук каждой программе.
 *  Если приложение получило отказ, а его последнего использования здесь нет или оно старое — до звукового устройства запрос не дошёл
 *  (так выглядит блокировка защитной программой: electron/electron#43524). */
export function parseConsentStore(text: string, wanted: RegExp): MicUse[] {
  const out: MicUse[] = [];
  let cur: MicUse | null = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (/^HKEY_/i.test(line)) {
      const key = line.split("\\").pop() ?? "";
      const app = key.replace(/#/g, "\\");
      cur = wanted.test(app) ? { app: basename(app.replace(/\\/g, "/")), lastStart: null, lastStop: null } : null;
      if (cur) out.push(cur);
      continue;
    }
    const m = /^(LastUsedTimeStart|LastUsedTimeStop)\s+REG_QWORD\s+(0x[0-9a-fA-F]+)/.exec(line);
    if (cur && m) { const iso = filetimeToIso(m[2]); if (m[1] === "LastUsedTimeStart") cur.lastStart = iso; else cur.lastStop = iso; }
  }
  return out;
}

async function microphoneUse(): Promise<MicUse[]> {
  if (process.platform !== "win32") return [];
  const out = await run("reg.exe", ["query", "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone\\NonPackaged", "/s"]);
  return parseConsentStore(out, /peregovorka|msedge\.exe|chrome\.exe/i).slice(0, 10);
}

export interface AudioDiag {
  native_log: boolean;
  wasapi: WasapiFinding[];
  recent: string[];
  security_products: string[];
  microphone_use: MicUse[];
}

/** Раздел `audio` отчёта «Диагностика»: коды WASAPI, последние строки журнала звука (маскированы), защитные программы, журнал доступа Windows. */
export async function audioDiag(app: App): Promise<AudioDiag> {
  const p = audioLogPath(app);
  const lines = [...tail(`${p}.old`, 128 * 1024), ...tail(p)];
  const audio = lines.filter((l) => /WAIS|AudioInput|getUserMedia|audio capture|MSM::/i.test(l));
  const [security_products, microphone_use] = await Promise.all([securityProducts(), microphoneUse()]);
  return { native_log: lines.length > 0, wasapi: wasapiFindings(audio), recent: redact(audio.slice(-60).join("\n")).split("\n").filter(Boolean),
    security_products, microphone_use };
}
