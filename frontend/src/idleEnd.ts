import { useEffect, useRef, useState } from "react";

/** Забытая встреча: человек остался в комнате один и ничего не делает. Правило считается на странице (только она видит действия человека), завершает сервер
 *  (`POST /meetings/{id}/idle-end` сам проверяет, что других людей нет). Встречи с несколькими людьми по тишине не завершаются: молча слушать — нормально.
 *  Сколько минут ждать — настройка комнаты (по умолчанию 30; 0 — не завершать). */
export const DEFAULT_IDLE_MIN = 30;
export const WARN_S = 120;
const TICK_MS = 5000;

/** Минуты из настроек клиента: новое поле idle_minutes; у прежнего сервера только idle_end ("off" — выключено). */
export function idleMinutes(c: { idle_minutes?: number; idle_end?: string } | undefined): number {
  if (!c) return 0;
  if (typeof c.idle_minutes === "number") return Math.max(0, c.idle_minutes);
  return c.idle_end === "off" ? 0 : DEFAULT_IDLE_MIN;
}

/** С какого момента показывать предупреждение: человек один и без действий minutes минут (что наступит позже); null — не показывать (выключено, человек не один). */
export function idleWarnAt(minutes: number, aloneSince: number | null, lastActive: number): number | null {
  if (minutes <= 0 || aloneSince === null) return null;
  return Math.max(aloneSince, lastActive) + minutes * 60_000;
}

const ACTIVITY = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"] as const;

/**
 * alone — кроме своих устройств в комнате никого; paused — нет связи, идёт эфир или встреча уже заканчивается (отсчёт сбрасывается);
 * busy — человек говорит или показывает экран (это действие, даже без мыши). Возвращает секунды до завершения, пока показано предупреждение.
 * Предупреждение видно не меньше WARN_S: после сна компьютера или редких тиков свёрнутой вкладки встреча не закрывается без него.
 */
export function useIdleEnd(minutes: number, alone: boolean, paused: boolean, busy: boolean, onEnd: (quietS: number) => void): { left: number | null; keep: () => void; quietS: () => number } {
  const aloneSince = useRef<number | null>(null);
  const lastActive = useRef(Date.now());
  const shownAt = useRef<number | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const endRef = useRef(onEnd);
  endRef.current = onEnd;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const active = minutes > 0 && alone && !paused;

  useEffect(() => {
    if (!active) { aloneSince.current = null; shownAt.current = null; setLeft(null); return; }
    aloneSince.current = Date.now();
    const touch = () => { lastActive.current = Date.now(); };
    ACTIVITY.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const tick = () => {
      const now = Date.now();
      if (busyRef.current) lastActive.current = now;
      const at = idleWarnAt(minutes, aloneSince.current, lastActive.current);
      if (at === null || now < at) { shownAt.current = null; setLeft(null); return; }
      shownAt.current ??= now;
      const rest = Math.ceil((shownAt.current + WARN_S * 1000 - now) / 1000);
      if (rest <= 0) { shownAt.current = null; setLeft(null); endRef.current(Math.round((now - lastActive.current) / 1000)); return; }
      setLeft(rest);
    };
    const t = window.setInterval(tick, TICK_MS);
    return () => { window.clearInterval(t); ACTIVITY.forEach((e) => window.removeEventListener(e, touch)); };
  }, [active, minutes]);

  const keep = () => { lastActive.current = Date.now(); shownAt.current = null; setLeft(null); };
  return { left, keep, quietS: () => Math.round((Date.now() - lastActive.current) / 1000) };
}
