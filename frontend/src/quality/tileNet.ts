/**
 * Качество связи участника на его плитке. Источник — `participant.connectionQuality` LiveKit: сервер сам оценивает канал КАЖДОГО участника до медиасервера
 * (потери, джиттер, RTT по его отчётам) и рассылает всем изменения — без опроса клиентов и без дополнительного трафика. Поэтому плохая связь одного участника
 * не отображается как плохая у всех. Свой приём (как этот браузер получает поток участника) — отдельно, только по наведению (getStats один раз).
 *
 * Уровни: 4 — отличное, 3 — хорошее, 2 — нестабильное, 1 — плохое (нестабильное держится дольше 15 с), «нет связи» — перечёркнутый значок, 0 — нет данных (серый).
 * Без мигания: ухудшение показывается, если держится 3 с, улучшение — 8 с; потеря связи — сразу.
 */
export type NetRaw = "excellent" | "good" | "poor" | "lost" | "unknown";
export interface NetShown { level: 0 | 1 | 2 | 3 | 4; lost: boolean; raw: NetRaw; rawSince: number; poorSince: number | null; changedAt: number }

export const DOWN_MS = 3000, UP_MS = 8000, BAD_AFTER_MS = 15000;

function target(raw: NetRaw, poorSince: number | null, now: number): { level: NetShown["level"]; lost: boolean } {
  if (raw === "lost") return { level: 0, lost: true };
  if (raw === "excellent") return { level: 4, lost: false };
  if (raw === "good") return { level: 3, lost: false };
  if (raw === "poor") return { level: poorSince !== null && now - poorSince >= BAD_AFTER_MS ? 1 : 2, lost: false };
  return { level: 0, lost: false };
}

/** Следующее показываемое состояние по сырому значению LiveKit и времени (вызывается на событие и раз в 2 с). */
export function stepNet(prev: NetShown | undefined, raw: NetRaw, now: number): NetShown {
  const rawSince = prev && prev.raw === raw ? prev.rawSince : now;
  const poorSince = raw === "poor" ? (prev?.poorSince ?? now) : null;
  const t = target(raw, poorSince, now);
  if (!prev) return { ...t, raw, rawSince, poorSince, changedAt: now };
  if (t.lost || prev.lost || prev.level === 0 || t.level === 0) {                  // потеря связи и «нет данных» — без задержки
    return t.lost === prev.lost && t.level === prev.level ? { ...prev, raw, rawSince, poorSince } : { ...t, raw, rawSince, poorSince, changedAt: now };
  }
  if (t.level === prev.level) return { ...prev, raw, rawSince, poorSince };
  const held = now - (t.level === 1 ? (poorSince ?? now) + BAD_AFTER_MS : rawSince);
  const need = t.level < prev.level ? DOWN_MS : UP_MS;
  return held >= need || (t.level === 1 && now - (poorSince ?? now) >= BAD_AFTER_MS) ? { ...t, raw, rawSince, poorSince, changedAt: now } : { ...prev, raw, rawSince, poorSince };
}

export function netLabel(s: NetShown | undefined): string {
  if (!s || (!s.lost && s.level === 0)) return "Нет данных о соединении";
  if (s.lost) return "Нет связи — переподключение";
  return ["", "Плохое соединение", "Нестабильное соединение", "Хорошее соединение", "Отличное соединение"][s.level];
}

export interface RecvStats { lossPct: number; jitterMs: number }
/** Подсказка: состояние канала участника и (для чужой плитки) как этот браузер получает его поток. Различает «плохо у участника» и «плохо у вас». */
export function netDetail(s: NetShown | undefined, recv: RecvStats | null, own: { rttMs?: number; lossPct?: number } | null): string {
  const parts = [netLabel(s) + (s && !s.lost && s.level >= 3 ? ": соединение стабильное" : "")];
  if (own) {
    if (own.rttMs !== undefined) parts.push(`${own.rttMs >= 300 ? "высокая задержка" : "задержка"} ${Math.round(own.rttMs)} мс`);
    if (own.lossPct !== undefined) parts.push(`${own.lossPct >= 3 ? "потеря пакетов" : "потери"} ${own.lossPct.toFixed(1)}%`);
  }
  if (recv) {
    const bad = recv.lossPct >= 3 || recv.jitterMs >= 30;
    parts.push(`приём у вас: потери ${recv.lossPct.toFixed(1)}%, джиттер ${Math.round(recv.jitterMs)} мс`);
    if (bad && s && s.level >= 3) parts.push("у участника связь в порядке — неполадки на вашей стороне");
  }
  return parts.join(" · ");
}
