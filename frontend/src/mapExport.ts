// Карта разговора: самостоятельный HTML-файл и поиск реплики-первоисточника. Чистые функции — проверяются тестами.
import type { Segment } from "./api";

export interface MapPayload { data: unknown; meta: Record<string, unknown> | null; canEdit?: boolean; categories?: unknown }

/** Что из сведений о формировании попадает в выгрузку: только для подписи «чем и за сколько сделано». Ни имён профилей API, ни кто нажал, ни адресов. */
const META_PUBLIC = ["model", "model_title", "started_at", "finished_at", "duration_s", "chunks", "retries", "warnings", "items_from_protocol"] as const;
export function publicMeta(meta: Record<string, unknown> | null | undefined): Record<string, unknown> | null {
  if (!meta) return null;
  const out: Record<string, unknown> = {};
  for (const k of META_PUBLIC) if (k in meta) out[k] = meta[k];
  return out;
}

/** JSON для вставки в <script type="application/json">: «</script>», «<!--» и разделители строк не могут закрыть тег или сломать разбор. */
export function jsonForScript(value: unknown): string {
  return JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026").replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** Скрипт внутрь <script>: последовательность «</script» в тексте файла закрыла бы тег. */
function inlineScript(js: string): string {
  return js.replace(/<\/(script)/gi, "<\\/$1");
}

/** Самостоятельный файл: данные, стили и скрипт внутри; открывается двойным щелчком, без сервера Peregovorka. Токенов, ключей и адресов API в нём нет. */
export function buildStandaloneHtml(title: string, css: string, js: string, payload: MapPayload): string {
  const body: MapPayload = { data: payload.data, meta: publicMeta(payload.meta) };
  return `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>
${css.replace(/<\/(style)/gi, "<\\/$1")}
</style>
</head>
<body>
<main id="app"></main>
<script type="application/json" id="pg-map-data">${jsonForScript(body)}</script>
<script>
${inlineScript(js)}
</script>
</body>
</html>
`;
}

/** Реплика стенограммы для перехода от источника: по id записи, иначе первая реплика не раньше указанной секунды от начала встречи. */
export function findSegment(segments: Segment[], startedAt: string, sec: number, segmentId?: number | null): Segment | null {
  if (!segments.length) return null;
  if (segmentId != null) {
    const hit = segments.find((s) => s.id === segmentId);
    if (hit) return hit;
  }
  const t = new Date(startedAt).getTime() + sec * 1000;
  return segments.find((s) => new Date(s.started_at).getTime() >= t - 1000) ?? segments[segments.length - 1];
}
