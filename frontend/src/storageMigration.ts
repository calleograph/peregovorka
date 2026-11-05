import type { StorageMigration } from "./api";

export const STATE_TITLE: Record<StorageMigration["state"], string> = {
  scanning: "Идёт оценка", ready: "Оценка готова", copying: "Копирование", attention: "Нужно решение", verifying: "Сверка", switching: "Переключение",
  switched: "Перенесено, источник сохранён", cleaning: "Очистка источника", cleaned: "Завершено", waiting: "Ждём хранилище", failed: "Ошибка", cancelled: "Отменено",
};
export const LIVE: StorageMigration["state"][] = ["scanning", "copying", "verifying", "switching", "cleaning", "waiting"];
export const isLive = (j: StorageMigration) => LIVE.includes(j.state);
export const isOpen = (j: StorageMigration) => isLive(j) || j.state === "ready" || j.state === "attention";

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return "меньше минуты";
  const h = Math.floor(seconds / 3600), m = Math.round((seconds % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин`;
}

/** Осталось по текущей скорости; до первых замеров — неизвестно. */
export const remaining = (j: StorageMigration): number | null => (j.speed_bps ? Math.max(0, (j.bytes_total - j.bytes_done) / j.speed_bps) : null);

export const enoughSpace = (j: StorageMigration): boolean => j.estimate?.target_free == null || j.estimate.target_free >= j.bytes_total;
