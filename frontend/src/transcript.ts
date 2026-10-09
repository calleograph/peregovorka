import type { Segment } from "./api";

/** Добавляет сегмент без дублей (по uid) и держит список упорядоченным по времени начала. */
export function mergeSegment(list: Segment[], seg: Segment): Segment[] {
  if (list.some((s) => s.uid === seg.uid)) return list;
  const next = [...list, seg];
  next.sort((a, b) => a.started_at.localeCompare(b.started_at) || a.id - b.id);
  return next;
}

export function mergeSegments(list: Segment[], incoming: Segment[]): Segment[] {
  return incoming.reduce(mergeSegment, list);
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Простой текстовый протокол: шапка со списком участников и реплики «[время] Имя: текст». */
/** Стенограмма обычным текстом, как на экране: «[время] Имя: реплика» по строке на реплику (без служебной разметки). */
export function transcriptText(segments: Segment[]): string {
  return segments.map((s) => `[${formatTime(s.started_at)}] ${s.display_name}: ${s.text}`).join("\n");
}

export function renderProtocol(roomName: string, startedAt: string, participants: string[], segments: Segment[]): string {
  const head = [
    `Переговорка: ${roomName}`,
    `Начало: ${new Date(startedAt).toLocaleString("ru-RU")}`,
    `Участвовали: ${participants.join(", ") || "—"}`,
    "",
  ];
  return head.concat(segments.map((s) => `[${formatTime(s.started_at)}] ${s.display_name}: ${s.text}`)).join("\n");
}

/** Вся накопленная транскрипция для буфера обмена: «ЧЧ:ММ:СС Имя» и на следующей строке реплика; без разметки и служебных идентификаторов. */
export function transcriptCopyText(segments: { started_at: string; display_name: string; text: string }[]): string {
  return segments.map((s) => `${formatTime(s.started_at)} ${s.display_name}\n${s.text}`).join("\n\n") + (segments.length ? "\n" : "");
}
