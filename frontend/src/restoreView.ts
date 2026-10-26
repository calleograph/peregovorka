// Восстановление материалов после переноса хранилища: чистая логика страницы (подписи, выбор, сводка). Правила восстановления — на сервере (services/restore.py).
export type Trust = "valid" | "foreign" | "unsigned" | "invalid" | "none";
export type ItemStatus = "new" | "partial" | "present" | "problem";

export interface RestoreFile { area: string; name: string; kind: string | null; state: string; size?: number | null }
export interface RestoreItem {
  id: string; dir: string; mode: "manifest" | "files"; trust: Trust; trust_note: string; status: ItemStatus; note: string; meeting_id: string | null;
  room: { name: string; id: string } | null; started_at: string | null; files: RestoreFile[]; problems: string[]; people: { found: number; missing: number }; how: string;
}
export interface RestoreJobView {
  id: string; kind: "scan" | "restore"; parent_id: string | null; status: "running" | "done" | "cancelled" | "failed" | "stale"; params: Record<string, unknown>; progress: Record<string, unknown>;
  error: string | null; started_by: string; created_at: string; finished_at: string | null; counts?: Record<string, number> | null;
  result?: { items?: RestoreItem[]; counts?: Record<string, number>; truncated?: boolean; report?: { id: string; dir: string; outcome: string; meeting_id?: string; lines: string[]; files?: string[] }[]; note?: string } | null;
}

export const TRUST: Record<Trust, { label: string; cls: string; certain: boolean }> = {
  valid: { label: "Достоверно: подпись верна", cls: "badge ok", certain: true },
  foreign: { label: "Предположительно: подпись другой установки", cls: "badge warn", certain: false },
  unsigned: { label: "Предположительно: паспорт без подписи", cls: "badge warn", certain: false },
  invalid: { label: "Предположительно: паспорт изменён", cls: "badge warn", certain: false },
  none: { label: "Предположительно: паспорта нет", cls: "badge warn", certain: false },
};

export const STATUS: Record<ItemStatus, { label: string; cls: string }> = {
  new: { label: "Новая", cls: "badge" }, partial: { label: "Не хватает материалов", cls: "badge warn" }, present: { label: "Уже в приложении", cls: "badge ok" }, problem: { label: "Есть проблемы", cls: "badge warn" },
};

export const KIND_LABEL: Record<string, string> = { audio_participant: "запись участника", audio_mix: "общая запись", transcript: "стенограмма", protocol: "протокол", summary: "резюме", chat: "переписка", board: "доска" };
export const FILE_STATE: Record<string, string> = { ok: "сумма совпала", ok_size: "размер совпал", found: "найден", mismatch: "не совпадает с паспортом", missing: "нет в каталоге", unreadable: "не читается" };

/** Что можно восстановить: новые и неполные встречи. «Уже в приложении» и «Есть проблемы» (нечего восстанавливать) не выбираются. */
export const selectable = (i: RestoreItem): boolean => i.status === "new" || i.status === "partial";

/** Выбор по умолчанию: достоверные новые и неполные. Предположительные администратор отмечает сам — они попадают в изолированную область. */
export const defaultSelection = (items: RestoreItem[]): Set<string> => new Set(items.filter((i) => selectable(i) && TRUST[i.trust].certain).map((i) => i.id));

export function summary(items: RestoreItem[], chosen: Set<string>): { certain: number; supposed: number } {
  let certain = 0, supposed = 0;
  for (const i of items) if (chosen.has(i.id)) { if (TRUST[i.trust].certain) certain++; else supposed++; }
  return { certain, supposed };
}

/** Файлы встречи по видам: «2 записи участников, стенограмма, протокол». */
export function filesText(files: RestoreFile[]): string {
  const n: Record<string, number> = {};
  for (const f of files) if (f.kind) n[f.kind] = (n[f.kind] ?? 0) + 1;
  const one: Record<string, string> = { audio_participant: "запись участника", audio_mix: "общая запись", transcript: "стенограмма", protocol: "протокол", summary: "резюме", chat: "переписка", board: "доска" };
  const many: Record<string, string> = { audio_participant: "записей участников", audio_mix: "общих записей" };
  return Object.entries(n).map(([k, c]) => (c === 1 ? one[k] ?? k : `${c} ${many[k] ?? one[k] ?? k}`)).join(", ") || "файлов нет";
}

export const jobTitle = (j: RestoreJobView): string => (j.kind === "scan" ? "Сканирование" : "Восстановление");
export const jobStatus = (s: RestoreJobView["status"]): string => ({ running: "идёт…", done: "готово", cancelled: "отменено", failed: "прервано", stale: "прервано (сервер перезапускался)" }[s] ?? s);
export const isActive = (j: RestoreJobView | null | undefined): boolean => !!j && j.status === "running";

const COUNT_LABEL: Record<string, string> = { found: "папок найдено", verified: "достоверно", supposed: "предположительно", new: "новых", partial: "неполных", present: "уже в приложении", problem: "с проблемами", dirs: "каталогов просмотрено",
  restored: "восстановлено достоверно", isolated: "в изолированную область", skipped: "пропущено", failed: "с ошибкой" };
/** Итог задания словами: «папок найдено: 3, достоверно: 1». Нулевые значения не показываются. */
export const countsText = (c: Record<string, number> | null | undefined): string => Object.entries(c ?? {}).filter(([, v]) => v).map(([k, v]) => `${COUNT_LABEL[k] ?? k}: ${v}`).join(", ");
