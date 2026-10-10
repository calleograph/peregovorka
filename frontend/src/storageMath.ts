/** Чистые функции блоков «Хранилище записей» и «Перенос данных» (проверяются тестами). */
import type { StorageVolume, TransferJob } from "./api";

export function volumeLabel(v: Pick<StorageVolume, "state" | "used_percent" | "stale">): { text: string; tone: "ok" | "warn" | "bad" | "" } {
  if (v.state === "not_configured") return { text: "не настроено", tone: "" };
  if (v.state === "unavailable") return { text: v.stale ? "недоступно (данные устарели)" : "недоступно", tone: "bad" };
  const p = v.used_percent;
  if (p != null && p >= 90) return { text: "почти заполнено", tone: "bad" };
  if (p != null && p >= 75) return { text: "место заканчивается", tone: "warn" };
  return { text: "в порядке", tone: "ok" };
}

export const DIRECTION_TITLE: Record<TransferJob["direction"], string> = { to_external: "Локальный диск → внешнее хранилище", to_local: "Внешнее хранилище → локальный диск" };

const STATE_TITLE: Record<string, string> = { queued: "в очереди", running: "выполняется", done: "завершён", failed: "остановлен", cancelled: "отменён" };
export const stateTitle = (s: string) => STATE_TITLE[s] ?? s;

/** Доля выполненного: перенесённые + пропущенные + с ошибкой от общего числа файлов. */
export function progressPercent(j: Pick<TransferJob, "total" | "done" | "skipped" | "failed">): number {
  if (!j.total) return j.total === 0 ? 100 : 0;
  return Math.min(100, Math.round(((j.done + j.skipped + j.failed) * 100) / j.total));
}

/** Что делать дальше (подсказка администратору) по итогам задания. */
export function nextStep(j: Pick<TransferJob, "state" | "skipped" | "failed" | "error">): string {
  if (j.state === "failed") return `Перенос остановлен: ${j.error ?? "причина не указана"}. Устраните причину и нажмите «Продолжить» — перенесённые файлы повторно не копируются.`;
  if (j.state === "done" && j.failed) return `Часть файлов не перенесена (${j.failed}) — файлы остались на прежнем месте. Причины ниже; после исправления запустите перенос снова.`;
  if (j.state === "done" && j.skipped) return `Пропущено файлов: ${j.skipped} (идёт встреча, запись ещё формируется или файл только что изменён). Запустите перенос позже.`;
  return "";
}
