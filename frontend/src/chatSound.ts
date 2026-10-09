/** Звуки уведомлений комнаты: новое сообщение чата (один мягкий тон) и общая громкость. Поднятая рука — отдельный двухтональный сигнал (handSound.ts).
 *  Настройки — личные, хранятся в браузере. Серия сообщений даёт ОДИН сигнал (слияние), собственные сообщения без звука. */
const KEY_ON = "pg:chatSound";
const KEY_VOL = "pg:soundVolume";
export const COALESCE_MS = 1500;

export function chatSoundEnabled(): boolean {
  try { return localStorage.getItem(KEY_ON) !== "off"; } catch { return true; }
}
export function setChatSoundEnabled(on: boolean): void {
  try { localStorage.setItem(KEY_ON, on ? "on" : "off"); } catch { /* хранилище недоступно — настройка не запоминается */ }
}

/** Громкость уведомлений 0…1 (по умолчанию 0,6). */
export function soundVolume(): number {
  try { const v = Number(localStorage.getItem(KEY_VOL)); return Number.isFinite(v) && localStorage.getItem(KEY_VOL) !== null ? Math.min(1, Math.max(0, v)) : 0.6; } catch { return 0.6; }
}
export function setSoundVolume(v: number): void {
  try { localStorage.setItem(KEY_VOL, String(Math.min(1, Math.max(0, v)))); } catch { /* не запоминается */ }
}

/** Решение «звучать ли»: не чаще раза в COALESCE_MS. Вынесено для проверки тестами. */
export function shouldPlay(now: number, last: number, enabled: boolean): boolean {
  return enabled && now - last >= COALESCE_MS;
}

let ctx: AudioContext | null = null;
let last = -Infinity;

export function audioContext(): AudioContext | null {
  try {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return null;
    ctx ??= new AC();
    if (ctx.state === "suspended") void ctx.resume();
    return ctx;
  } catch { return null; }
}

function tone(c: AudioContext, freq: number, at: number, dur: number, peak: number, type: OscillatorType): void {
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = type; o.frequency.value = freq;
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), at + 0.025);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  o.connect(g).connect(c.destination);
  o.start(at); o.stop(at + dur + 0.02);
}

/** Новое сообщение: один короткий мягкий тон (~0,22 с), заметно отличается от «руки» (там два восходящих тона). */
export function playChatSound(force = false): void {
  const now = Date.now();
  if (!force && !shouldPlay(now, last, chatSoundEnabled())) return;
  last = now;
  const c = audioContext();
  if (!c) return;
  try { tone(c, 587, c.currentTime, 0.24, 0.1 * soundVolume(), "triangle"); } catch { /* звук недоступен — сообщение всё равно видно */ }
}
