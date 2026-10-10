/** Чистые функции плеера записей (проверяются тестами): время, скорость, громкость, клавиши. */
export const SPEEDS = [0.75, 1, 1.25, 1.5, 2] as const;

/** 3725 → «1:02:05», 65 → «1:05», 5 → «0:05». Неопределённое/бесконечное — «0:00». */
export function fmtTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(r).padStart(2, "0")}`;
}

export const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

export function nextSpeed(cur: number, dir: 1 | -1): number {
  const i = SPEEDS.findIndex((s) => s === cur);
  const at = i < 0 ? SPEEDS.findIndex((s) => s === 1) : i;
  return SPEEDS[clamp(at + dir, 0, SPEEDS.length - 1)];
}

export type KeyAction = { type: "toggle" } | { type: "seek"; by: number } | { type: "volume"; by: number } | { type: "mute" } | { type: "fullscreen" } | { type: "speed"; dir: 1 | -1 } | { type: "home" } | { type: "end" } | null;

/** Клавиши плеера: пробел/K — пауза, ←/→ — 5 с (с Shift — 30 с), ↑/↓ — громкость, M — звук, F — во весь экран, «,» «.» — скорость, Home/End — начало/конец. Esc обрабатывает окно. */
export function keyAction(e: { key: string; shiftKey?: boolean; ctrlKey?: boolean; altKey?: boolean; metaKey?: boolean }): KeyAction {
  if (e.ctrlKey || e.altKey || e.metaKey) return null;
  const big = e.shiftKey ? 30 : 5;
  switch (e.key) {
    case " ": case "k": case "K": return { type: "toggle" };
    case "ArrowLeft": return { type: "seek", by: -big };
    case "ArrowRight": return { type: "seek", by: big };
    case "ArrowUp": return { type: "volume", by: 0.1 };
    case "ArrowDown": return { type: "volume", by: -0.1 };
    case "m": case "M": return { type: "mute" };
    case "f": case "F": return { type: "fullscreen" };
    case ",": case "<": return { type: "speed", dir: -1 };
    case ".": case ">": return { type: "speed", dir: 1 };
    case "Home": return { type: "home" };
    case "End": return { type: "end" };
    default: return null;
  }
}

/** Понятное сообщение по ответу сервера на запрос файла записи. */
export function mediaErrorText(status: number, detail?: string): string {
  if (status === 409) return detail || "Запись ещё формируется или не удалась.";
  if (status === 410) return detail || "Файл записи удалён из хранилища.";
  if (status === 503) return "Хранилище записей сейчас недоступно. Повторите позже.";
  if (status === 404 || status === 403) return "Запись недоступна: нет доступа или она удалена.";
  if (status === 401) return "Сеанс истёк — войдите снова.";
  return "Не удалось загрузить запись. Проверьте соединение и попробуйте ещё раз.";
}
