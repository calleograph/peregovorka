/** Тихий короткий сигнал «рука поднята»: два мягких тона, ~0,35 с, громкость невысокая. Включается и выключается настройкой пользователя (хранится в браузере). */
import { soundVolume } from "./chatSound";

const KEY = "pg:handSound";

export function handSoundEnabled(): boolean {
  try { return localStorage.getItem(KEY) !== "off"; } catch { return true; }
}

export function setHandSoundEnabled(on: boolean): void {
  try { localStorage.setItem(KEY, on ? "on" : "off"); } catch { /* хранилище недоступно — настройка не запоминается */ }
}

let ctx: AudioContext | null = null;
let last = 0;

/** Один сигнал на событие; чаще раза в две секунды не звучит (несколько рук подряд — один звук). */
export function playHandSound(force = false): void {
  if (!force && !handSoundEnabled()) return;
  const now = Date.now();
  if (!force && now - last < 2000) return;
  last = now;
  try {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    ctx ??= new AC();
    if (ctx.state === "suspended") void ctx.resume();
    const t = ctx.currentTime;
    for (const [freq, at] of [[660, 0], [880, 0.14]] as const) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine"; o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, t + at);
      g.gain.exponentialRampToValueAtTime(Math.max(0.0002, 0.12 * soundVolume()), t + at + 0.03);
      g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.2);
      o.connect(g).connect(ctx.destination);
      o.start(t + at); o.stop(t + at + 0.22);
    }
  } catch { /* звук недоступен — рука всё равно показана на плитке */ }
}
