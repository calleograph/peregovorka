/**
 * Подробная проверка захвата микрофона для поддержки и сравнения клиентов: один и тот же код запускается в Microsoft Edge и в приложении Windows, отчёты сравниваются.
 * Показывает то, что видит сам браузерный движок: версия Chromium, устройства Windows («По умолчанию» и «Связь» — отдельные записи), время и успех повторных открытий,
 * фактические параметры захвата, одновременное открытие устройства вторым потоком, запуск AudioContext с устройством вывода и без него.
 * Содержимого разговоров нет: только технические значения и названия устройств. Этот код сам ничего не «чинит» — он собирает факты.
 */
import { captureOptions, loadMicPrefs } from "./micPrefs";

export interface OpenTry { ok: boolean; ms: number; error?: string; settings?: Record<string, string | number | boolean | undefined>; muted?: boolean; peak?: number }
export interface MicProbeReport {
  at: string;
  env: { chromium: string; brands: string; platform: string; desktopApp: string | null; secure: boolean; permission: string };
  devices: { kind: string; label: string; role: "default" | "communications" | "device"; group: string }[];
  opens: OpenTry[];
  concurrent: { first: OpenTry; second: OpenTry };
  audioContext: { sinkNone: { supported: boolean; ms?: number; state?: string; error?: string }; sinkDefault: { ms?: number; state?: string; baseLatency?: number; outputLatency?: number; sampleRate?: number; error?: string } };
  totalMs: number;
}

const short = (v: unknown, n = 140) => String((v as Error)?.name ? `${(v as Error).name}: ${(v as Error).message}` : v).slice(0, n);
const pick = (s: MediaTrackSettings) => ({ device: s.deviceId?.slice(0, 8), sampleRate: s.sampleRate, channels: s.channelCount, latency: (s as { latency?: number }).latency, echoCancellation: s.echoCancellation, noiseSuppression: s.noiseSuppression, autoGainControl: s.autoGainControl });
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function peakOf(stream: MediaStream, ms = 800): Promise<number> {
  const Ctx = window.AudioContext;
  const ctx = new Ctx();
  try {
    const an = ctx.createAnalyser(); an.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(an);
    const buf = new Float32Array(an.fftSize); let peak = 0;
    for (let t = 0; t < ms; t += 50) { await wait(50); an.getFloatTimeDomainData(buf); for (const v of buf) peak = Math.max(peak, Math.abs(v)); }
    return Math.round(peak * 1000) / 1000;
  } finally { void ctx.close().catch(() => undefined); }
}

async function openOnce(withPeak: boolean): Promise<{ r: OpenTry; stream?: MediaStream }> {
  const t0 = performance.now();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: captureOptions(loadMicPrefs()) as MediaTrackConstraints });
    const tr = stream.getAudioTracks()[0];
    const r: OpenTry = { ok: true, ms: Math.round(performance.now() - t0), settings: pick(tr.getSettings()), muted: tr.muted };
    if (withPeak) r.peak = await peakOf(stream).catch(() => undefined);
    return { r, stream };
  } catch (e) { return { r: { ok: false, ms: Math.round(performance.now() - t0), error: short(e) } }; }
}
const stopAll = (s?: MediaStream) => s?.getTracks().forEach((t) => t.stop());

async function ctxTry(sinkNone: boolean): Promise<{ ms?: number; state?: string; baseLatency?: number; outputLatency?: number; sampleRate?: number; error?: string }> {
  const t0 = performance.now();
  try {
    const ctx = new AudioContext({ latencyHint: "interactive", ...(sinkNone ? { sinkId: { type: "none" } } : {}) } as AudioContextOptions);
    await Promise.race([ctx.resume(), wait(2000)]);
    const out = { ms: Math.round(performance.now() - t0), state: ctx.state, baseLatency: ctx.baseLatency, outputLatency: ctx.outputLatency, sampleRate: ctx.sampleRate };
    await ctx.close().catch(() => undefined);
    return out;
  } catch (e) { return { error: short(e) }; }
}

export async function runMicProbe(cycles = 4): Promise<MicProbeReport> {
  const t0 = performance.now();
  const ua = navigator.userAgent;
  const uad = (navigator as Navigator & { userAgentData?: { brands?: { brand: string; version: string }[]; platform?: string } }).userAgentData;
  let permission = "unknown";
  try { permission = (await navigator.permissions.query({ name: "microphone" as PermissionName })).state; } catch { /* API недоступно */ }
  const env = {
    chromium: /Chrome\/([\d.]+)/.exec(ua)?.[1] ?? "?", brands: (uad?.brands ?? []).map((b) => `${b.brand} ${b.version}`).join(", ").slice(0, 160),
    platform: uad?.platform ?? navigator.platform, desktopApp: /PeregovorkaDesktop\/([\d.]+)/.exec(ua)?.[1] ?? null, secure: window.isSecureContext, permission,
  };
  const opens: OpenTry[] = [];
  for (let i = 0; i < cycles; i++) { const { r, stream } = await openOnce(i === 0); stopAll(stream); opens.push(r); await wait(150); }
  const devs = await navigator.mediaDevices.enumerateDevices().catch(() => [] as MediaDeviceInfo[]);                    // после открытия: названия уже доступны
  const devices = devs.filter((d) => d.kind !== "videoinput").map((d) => ({ kind: d.kind, label: d.label.slice(0, 80), role: d.deviceId === "default" ? "default" as const : d.deviceId === "communications" ? "communications" as const : "device" as const, group: d.groupId.slice(0, 6) }));
  const a = await openOnce(false), b = await openOnce(false);
  stopAll(a.stream); stopAll(b.stream);
  const sinkNone = "sinkId" in AudioContext.prototype ? await ctxTry(true) : { ms: undefined, state: undefined, error: undefined };
  const sinkDefault = await ctxTry(false);
  return { at: new Date().toISOString(), env, devices, opens, concurrent: { first: a.r, second: b.r },
    audioContext: { sinkNone: { supported: "sinkId" in AudioContext.prototype, ...sinkNone }, sinkDefault }, totalMs: Math.round(performance.now() - t0) };
}

/** Короткий текст отчёта: его удобно вставить в письмо; полный JSON — по кнопке копирования. */
export function summarizeMicProbe(r: MicProbeReport): string[] {
  const ok = r.opens.filter((o) => o.ok), ms = ok.map((o) => o.ms);
  const lines = [
    `Движок: Chromium ${r.env.chromium}${r.env.desktopApp ? ` · приложение Windows ${r.env.desktopApp}` : " · браузер"} · ${r.env.platform} · доступ к микрофону: ${r.env.permission}`,
    `Открытий микрофона: ${ok.length} из ${r.opens.length}${ms.length ? `, время ${Math.min(...ms)}–${Math.max(...ms)} мс` : ""}${r.opens.find((o) => !o.ok) ? ` · ошибка: ${r.opens.find((o) => !o.ok)!.error}` : ""}`,
  ];
  const s = ok[0]?.settings;
  if (s) lines.push(`Захват: ${s.sampleRate ?? "?"} Гц, каналов ${s.channels ?? "?"}, задержка ${s.latency ?? "?"} с; сигнал в первом открытии ${ok[0].peak === undefined ? "не измерен" : ok[0].peak < 0.001 ? "сигнала не было (пик < 0,001): говорите во время проверки" : `есть (пик ${ok[0].peak})`}`);
  lines.push(`Второй одновременный поток с тем же устройством: ${r.concurrent.second.ok ? "открылся" : `не открылся (${r.concurrent.second.error})`}`);
  const sd = r.audioContext.sinkDefault, sn = r.audioContext.sinkNone;
  lines.push(`AudioContext с устройством вывода: ${sd.error ?? `${sd.state}, ${sd.ms} мс`}${sn.supported ? ` · без устройства вывода: ${sn.error ?? `${sn.state}, ${sn.ms} мс`}` : " · без устройства вывода: не поддерживается"}`);
  lines.push(`Устройства Windows: микрофонов ${r.devices.filter((d) => d.kind === "audioinput").length} (по умолчанию: ${r.devices.some((d) => d.kind === "audioinput" && d.role === "default") ? "есть" : "нет"}, «Связь»: ${r.devices.some((d) => d.kind === "audioinput" && d.role === "communications") ? "есть" : "нет"})`);
  return lines;
}
