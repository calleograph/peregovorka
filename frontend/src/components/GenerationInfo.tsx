import type { ProtocolItem } from "../api";
import { clock, spanRu } from "../util";

/** Итог формирования одним словом: готово / ошибка / обрезано / с предупреждением. Обрезанный документ не выглядит полноценным. */
export function docState(p: ProtocolItem): { label: string; tone: "ok" | "warn" | "error" | "pending" } {
  if (p.status === "pending") return { label: "в очереди", tone: "pending" };
  if (p.status === "running") return { label: "формируется", tone: "pending" };
  if (p.status === "failed") return { label: "ошибка", tone: "error" };
  if (p.status === "timeout") return { label: "превышено время", tone: "error" };
  if (p.status === "cancelled") return { label: "отменено", tone: "warn" };
  if (p.coverage && p.coverage.done < p.coverage.total) return { label: `неполное, ${p.coverage.percent}%`, tone: "warn" };
  if (p.truncated) return { label: "обрезано", tone: "error" };
  if (p.warnings && p.warnings.length) return { label: "готово, есть предупреждения", tone: "warn" };
  return { label: p.edited_at ? "отредактирован" : "готово", tone: "ok" };
}

/** Одна строка для списка документов: «gpt-x · «Шлюз» · обезличено · начато 10:42:13 · готово 10:46:51 · 4 мин 38 с». */
export function generationLine(p: ProtocolItem): string {
  const g = p.generation, t = p.timing;
  const model = g?.model_title || g?.model || p.model;
  const parts: string[] = [];
  if (model) parts.push(model);
  if (g?.llm_profile) parts.push(`«${g.llm_profile}»`);
  if (g?.anonymized) parts.push("обезличено");
  if (t?.requested_at) parts.push(`начато ${clock(t.requested_at)}`);
  if (p.status !== "pending" && p.status !== "running") {
    const word = { ready: "готово", failed: "ошибка", timeout: "остановлено по времени", cancelled: "отменено" }[p.status];
    if (t?.finished_at) parts.push(`${word} ${clock(t.finished_at)}`);
    if (t?.total_s !== null && t?.total_s !== undefined) parts.push(spanRu(t.total_s));
  }
  return parts.join(" · ");
}

const CATEGORY_RU: Record<string, string> = { person: "люди", organization: "организации", location: "места", address: "адреса", phone: "телефоны", email: "почта", ip: "IP",
  host: "серверы", url: "ссылки", login: "учётные записи", id_number: "документы", bank: "реквизиты", doc_number: "номера", money: "суммы", project: "проекты", custom: "свои термины", secret: "секреты" };
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
  if (g?.model) rows.push(["Модель", `${g.model_title || g.model}${g.model_title && g.model !== g.model_title ? ` (${g.model})` : ""} · API: ${g.api_type ?? "?"}${g.llm_profile ? `, подключение «${g.llm_profile}»` : ""}`]);
  if (g?.anonymized !== undefined && g?.anonymized !== null) rows.push(["Обезличивание", g.anonymized
    ? `${g.anonymizer_profile ? `профиль «${g.anonymizer_profile}»` : "включено"}${g.anonymized_replaced ? ` · замен: ${g.anonymized_replaced}` : ""}${g.anonymized_categories ? ` (${Object.entries(g.anonymized_categories).map(([k, n]) => `${CATEGORY_RU[k] ?? k} ${n}`).join(", ")})` : ""}`
    : "выключено в переговорке (пароли и ключи скрываются всегда)"]);
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
