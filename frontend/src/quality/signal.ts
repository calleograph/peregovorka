import type { VideoLevel } from "./adaptive";

export type SignalTone = "ok" | "warn" | "bad";
export interface Signal { bars: 1 | 2 | 3 | 4; tone: SignalTone; label: string }

/**
 * Индикатор уровня сигнала в шапке комнаты. Строится по УРОВНЮ видео, который выбирает адаптивный контроллер (у него уже есть гистерезис: вниз — после двух плохих замеров,
 * вверх — после шести хороших), а не по мгновенному замеру: значок не мигает. Потеря связи — красный сразу.
 * Высокое — зелёный (4), среднее — жёлтый (3), низкое — жёлтый (2), минимальное/«только звук»/нет связи — красный (1).
 */
export function signalOf(level: VideoLevel, conn: string): Signal {
  if (conn === "lost") return { bars: 1, tone: "bad", label: "связь потеряна" };
  switch (level) {
    case "high": return { bars: 4, tone: "ok", label: "хорошая" };
    case "medium": return { bars: 3, tone: "warn", label: "средняя" };
    case "low": return { bars: 2, tone: "warn", label: "слабая" };
    default: return { bars: 1, tone: "bad", label: "плохая" };
  }
}
