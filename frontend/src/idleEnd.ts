import { useEffect, useRef, useState } from "react";

/** Забытая встреча: человек остался в комнате один и ничего не делает. Правило считается на странице (только она видит действия человека), завершает сервер
 *  (`POST /meetings/{id}/idle-end` сам проверяет, что других людей нет). Встречи с несколькими людьми по тишине не завершаются: молча смотреть демонстрацию — нормально. */
export type IdleMode = "standard" | "careful" | "off";
export const IDLE_RULES: Record<Exclude<IdleMode, "off">, { aloneMin: number; quietMin: number }> = {
  standard: { aloneMin: 15, quietMin: 10 },
  careful: { aloneMin: 30, quietMin: 30 },
};
export const WARN_S = 120;
const TICK_MS = 5000;

/** С какого момента показывать предупреждение; null — не показывать (режим выключен, человек не один). */
export function idleWarnAt(mode: IdleMode | undefined, aloneSince: number | null, lastActive: number): number | null {
  if (mode === "off" || aloneSince === null) return null;
  const r = IDLE_RULES[mode ?? "standard"] ?? IDLE_RULES.standard;
  return Math.max(aloneSince + r.aloneMin * 60_000, lastActive + r.quietMin * 60_000);
}

const ACTIVITY = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"] as const;

/**
 * alone — кроме своих устройств в комнате никого; paused — нет связи, идёт эфир или встреча уже заканчивается (отсчёт сбрасывается);
 * busy — человек говорит или показывает экран (это действие, даже без мыши). Возвращает секунды до завершения, пока показано предупреждение.
 * Предупреждение видно не меньше WARN_S: после сна компьютера или редких тиков свёрнутой вкладки встреча не закрывается без него.
 */
export function useIdleEnd(mode: IdleMode | undefined, alone: boolean, paused: boolean, busy: boolean, onEnd: () => void): { left: number | null; keep: () => void } {
  const aloneSince = useRef<number | null>(null);
  const lastActive = useRef(Date.now());
  const shownAt = useRef<number | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const endRef = useRef(onEnd);
  endRef.current = onEnd;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const active = mode !== "off" && alone && !paused;

  useEffect(() => {
    if (!active) { aloneSince.current = null; shownAt.current = null; setLeft(null); return; }
    aloneSince.current = Date.now();
    const touch = () => { lastActive.current = Date.now(); };
    ACTIVITY.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const tick = () => {
      const now = Date.now();
      if (busyRef.current) lastActive.current = now;
      const at = idleWarnAt(mode, aloneSince.current, lastActive.current);
      if (at === null || now < at) { shownAt.current = null; setLeft(null); return; }
      shownAt.current ??= now;
      const rest = Math.ceil((shownAt.current + WARN_S * 1000 - now) / 1000);
      if (rest <= 0) { shownAt.current = null; setLeft(null); endRef.current(); return; }
      setLeft(rest);
    };
    const t = window.setInterval(tick, TICK_MS);
    return () => { window.clearInterval(t); ACTIVITY.forEach((e) => window.removeEventListener(e, touch)); };
  }, [active, mode]);

  const keep = () => { lastActive.current = Date.now(); shownAt.current = null; setLeft(null); };
  return { left, keep };
}
