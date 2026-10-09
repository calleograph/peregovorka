/** Сигнал «рука поднята»: три восходящих тона, явно слышен. Включается и выключается настройкой пользователя (хранится в браузере). */
import { playNotes } from "./chatSound";
import { HAND_NOTES } from "./soundSpec";

const KEY = "pg:handSound";

export function handSoundEnabled(): boolean {
  try { return localStorage.getItem(KEY) !== "off"; } catch { return true; }
}

export function setHandSoundEnabled(on: boolean): void {
  try { localStorage.setItem(KEY, on ? "on" : "off"); } catch { /* хранилище недоступно — настройка не запоминается */ }
}

let last = 0;

/** Один сигнал на событие; чаще раза в две секунды не звучит (несколько рук подряд — один звук). Три восходящих тона (~0,75 с). */
export function playHandSound(force = false): void {
  if (!force && !handSoundEnabled()) return;
  const now = Date.now();
  if (!force && now - last < 2000) return;
  last = now;
  playNotes(HAND_NOTES);
}
