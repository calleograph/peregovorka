/** Звуки уведомлений комнаты: новое сообщение чата (один мягкий тон) и общая громкость. Поднятая рука — отдельный двухтональный сигнал (handSound.ts).
 *  Настройки — личные, хранятся в браузере. Серия сообщений даёт ОДИН сигнал (слияние), собственные сообщения без звука. */
import { CHAT_NOTES, DEFAULT_VOLUME, peakAt, type Note } from "./soundSpec";

const KEY_ON = "pg:chatSound";
const KEY_VOL = "pg:soundVolume";
export const COALESCE_MS = 1500;

export function chatSoundEnabled(): boolean {
  try { return localStorage.getItem(KEY_ON) !== "off"; } catch { return true; }
}
export function setChatSoundEnabled(on: boolean): void {
  try { localStorage.setItem(KEY_ON, on ? "on" : "off"); } catch { /* хранилище недоступно — настройка не запоминается */ }
}

/** Громкость уведомлений 0…1 (по умолчанию 0,8: сигналы должны быть слышны поверх голосов). */
export function soundVolume(): number {
  try { const v = Number(localStorage.getItem(KEY_VOL)); return Number.isFinite(v) && localStorage.getItem(KEY_VOL) !== null ? Math.min(1, Math.max(0, v)) : DEFAULT_VOLUME; } catch { return DEFAULT_VOLUME; }
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

function schedule(c: AudioContext, notes: Note[], vol: number): void {
  const t0 = c.currentTime + 0.02;
  for (const n of notes) {
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = n.type; o.frequency.value = n.freq;
    const at = t0 + n.at;
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peakAt(n, vol), at + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, at + n.dur);
    o.connect(g).connect(c.destination);
    o.start(at); o.stop(at + n.dur + 0.03);
  }
}

/** Сыграть набор нот. Если браузер ещё не разрешил звук (контекст «приостановлен» до первого действия пользователя), сначала возобновляем его и только потом играем — иначе сигнал пропадает. */
export function playNotes(notes: Note[]): void {
  const c = audioContext();
  if (!c) return;
  const vol = soundVolume();
  try {
    if (c.state === "running") schedule(c, notes, vol);
    else void c.resume().then(() => schedule(c, notes, vol)).catch(() => undefined);
  } catch { /* звук недоступен — событие всё равно видно на экране */ }
}

/** Новое сообщение: два нисходящих тона «дин-дон» (~0,5 с), явно слышны и не похожи на «руку» (три восходящих). */
export function playChatSound(force = false): void {
  const now = Date.now();
  if (!force && !shouldPlay(now, last, chatSoundEnabled())) return;
  last = now;
  playNotes(CHAT_NOTES);
}
