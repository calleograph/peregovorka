import type { ProtocolItem } from "../api";
import { clock, spanRu } from "../util";

/** Итог формирования одним словом: готово / ошибка / обрезано / с предупреждением. Обрезанный документ не выглядит полноценным. */
export function docState(p: ProtocolItem): { label: string; tone: "ok" | "warn" | "error" | "pending" } {
  if (p.status === "pending") return { label: "создаётся", tone: "pending" };
  if (p.status === "failed") return { label: "ошибка", tone: "error" };
  if (p.truncated) return { label: "обрезано", tone: "error" };
  if (p.warnings && p.warnings.length) return { label: "готово, есть предупреждения", tone: "warn" };
  return { label: p.edited_at ? "отредактирован" : "готово", tone: "ok" };
}

/** Одна строка для списка документов: «Qwen3 1.7B · локальная · начато 10:42:13 · готово 10:46:51 · 4 мин 38 с». */
export function generationLine(p: ProtocolItem): string {
  const g = p.generation, t = p.timing;
  const model = g?.model_title || g?.model || p.model;
  const parts: string[] = [];
  if (model) parts.push(model);
  if (g?.llm_local === true) parts.push("локальная");
  else if (g?.llm_local === false) parts.push(g.llm_profile ? `внешняя API «${g.llm_profile}»` : "внешняя API");
  if (t?.requested_at) parts.push(`начато ${clock(t.requested_at)}`);
  if (p.status === "ready" || p.status === "failed") {
    if (t?.finished_at) parts.push(`${p.status === "failed" ? "ошибка" : "готово"} ${clock(t.finished_at)}`);
    if (t?.total_s !== null && t?.total_s !== undefined) parts.push(spanRu(t.total_s));
  }
  return parts.join(" · ");
}

const SOURCE: Record<string, string> = { once: "выбрана при формировании", meeting: "выбрана для встречи", room: "выбрана для переговорки", system: "системная по умолчанию" };
const FINISH: Record<string, string> = { stop: "завершён", length: "оборван по лимиту длины", end_turn: "завершён", max_tokens: "оборван по лимиту длины" };

/** Подробности формирования: времена этапов, очередь и работа модели, части и повторы, причины остановки. Для журнала и диагностики. */
export default function GenerationInfo({ item }: { item: ProtocolItem }) {
  const g = item.generation, t = item.timing;
  if (!g?.model && !t?.requested_at) return null;
  const rows: [string, string][] = [];
  if (t?.requested_at) rows.push(["Нажато «Сформировать»", clock(t.requested_at)]);
  if (t?.started_at) rows.push(["Задача взята в работу", `${clock(t.started_at)}${t.queue_s ? ` (ожидание ${spanRu(t.queue_s)})` : ""}`]);
  if (t?.llm_started_at) rows.push(["Модель начала работу", `${clock(t.llm_started_at)}${t.prepare_s ? ` (подготовка текста ${spanRu(t.prepare_s)})` : ""}`]);
  if (t?.llm_finished_at) rows.push(["Модель закончила", `${clock(t.llm_finished_at)}${t.llm_s !== null ? ` (работа модели ${spanRu(t.llm_s)})` : ""}`]);
  if (t?.finished_at) rows.push(["Документ получен", `${clock(t.finished_at)} · всего ${spanRu(t.total_s)}`]);
  if (g?.model) rows.push(["Модель", `${g.model_title || g.model}${g.model_title && g.model !== g.model_title ? ` (${g.model})` : ""} · ${g.llm_local ? "локальная" : `внешняя, API: ${g.api_type ?? "?"}${g.llm_profile ? `, профиль «${g.llm_profile}»` : ""}`}`]);
  if (g?.llm_source) rows.push(["Почему эта модель", SOURCE[g.llm_source] ?? g.llm_source]);
  if (g?.input_chars) rows.push(["Стенограмма → документ", `${g.input_chars.toLocaleString("ru-RU")} знаков → ${g.chunks ?? 1} ${(g.chunks ?? 1) === 1 ? "часть" : "частей"} → ${(g.llm_calls ?? 0)} обращений к модели${g.retries ? `, повторов: ${g.retries}` : ""} → ${(g.output_chars ?? 0).toLocaleString("ru-RU")} знаков`]);
  if (g?.finish && Object.keys(g.finish).length) rows.push(["Причины остановки модели", Object.entries(g.finish).map(([k, n]) => `${FINISH[k] ?? k}: ${n}`).join("; ") + (g.max_tokens ? ` · предел ответа ${g.max_tokens} токенов${g.limit_note ? ` (${g.limit_note})` : ""}` : "")]);
  if (g?.prompt_tokens || g?.completion_tokens) rows.push(["Токены", `вход ${g.prompt_tokens ?? 0} · выход ${g.completion_tokens ?? 0}`]);
  if (!rows.length) return null;
  return (
    <details style={{ marginTop: 8 }}>
      <summary className="muted small">Как создан документ: время, модель, части</summary>
      <table className="table small" style={{ marginTop: 6 }}><tbody>
        {rows.map(([k, v]) => <tr key={k}><th style={{ whiteSpace: "nowrap", textAlign: "left" }}>{k}</th><td>{v}</td></tr>)}
      </tbody></table>
    </details>
  );
}
