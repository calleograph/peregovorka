import { useEffect, useState } from "react";
import { api, type ApiError, type EffectiveModel, type EffectiveModels, type ModelStat } from "../../api";
import { fmtDate } from "./common";
import { spanRu } from "../../util";

const API_TYPE: Record<string, string> = { local: "локальная (llama.cpp)", openai: "OpenAI", anthropic: "Anthropic", openai_compatible: "OpenAI-совместимый", custom: "свой OpenAI-подобный" };

function Row({ label, m, own }: { label: string; m: EffectiveModel; own: number }) {
  return (
    <tr>
      <th style={{ textAlign: "left" }}>{label}</th>
      <td>{m.enabled ? <><b>{m.name}</b>{m.model && !m.local ? <span className="muted small"> · {m.model}</span> : null}</> : <span className="muted">отключено</span>}</td>
      <td>{m.enabled ? (m.local ? "локальная, данные остаются на сервере" : `внешняя, ${API_TYPE[m.api_type ?? ""] ?? m.api_type}`) : "—"}</td>
      <td>{m.max_output_tokens ? `${m.max_output_tokens} токенов${m.max_output_note ? ` (${m.max_output_note})` : ""}` : "—"}</td>
      <td>{!m.enabled ? <span className="badge">выключена</span> : m.ready ? <span className="badge ok">готова</span> : <span className="badge warn" title={m.problem ?? ""}>{m.problem ?? "не готова"}</span>}</td>
      <td className="small">{own ? `в ${own} перегов. выбрана своя` : "везде эта"}</td>
    </tr>
  );
}

/** «Какая модель будет вызвана»: по одной строке на задачу. Администратор сразу видит, чем будет формироваться протокол и резюме по умолчанию. */
export function LlmEffectivePanel() {
  const [d, setD] = useState<EffectiveModels | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { void api.admin.effectiveModels().then(setD).catch((e) => setErr((e as ApiError).message)); }, []);
  return (
    <section className="card">
      <h2>Какая модель будет вызвана</h2>
      <p className="muted small" style={{ marginTop: 0 }}>Порядок выбора при формировании документа: <b>модель, выбранная в окне «Сформировать» на один раз</b> → настройка конкретной встречи → настройка переговорки → <b>системное значение по умолчанию</b> (в таблице). Там, где ничего не выбрано, работает модель из таблицы.</p>
      {err && <div className="alert error">{err}</div>}
      {!d && !err && <div className="muted">Загрузка…</div>}
      {d && (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr><th>Задача</th><th>Модель по умолчанию</th><th>Откуда</th><th>Предел длины ответа</th><th>Состояние</th><th>В переговорках</th></tr></thead>
            <tbody>
              <Row label="Протокол" m={d.protocol} own={d.rooms_with_own_model.protocol} />
              <Row label="Краткое резюме" m={d.summary} own={d.rooms_with_own_model.summary} />
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const KIND: Record<string, string> = { protocol: "протокол", summary: "резюме" };

/** Показатели моделей по созданным документам: количество, ошибки, оборванные ответы, время, длина входа и выхода, скорость. */
export function LlmStatsPanel() {
  const [rows, setRows] = useState<ModelStat[] | null>(null);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState("");
  useEffect(() => { void api.admin.modelStats().then((r) => { setRows(r.models); setTotal(r.documents); }).catch((e) => setErr((e as ApiError).message)); }, []);
  return (
    <section className="card">
      <h2>Показатели моделей</h2>
      <p className="muted small" style={{ marginTop: 0 }}>По уже созданным документам (последние {total || "—"}). Чем их больше, тем точнее прогноз времени в окне «Сформировать». Загрузка процессора и памяти локальной модели здесь не собирается; скорость в токенах в секунду считается по ответам провайдера.</p>
      {err && <div className="alert error">{err}</div>}
      {rows && rows.length === 0 && <div className="muted">Документов пока нет.</div>}
      {rows && rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr><th>Модель</th><th>Задача</th><th>Документов</th><th>Ошибок</th><th>Оборвано</th><th>Повторов</th><th>Время: среднее / медиана</th><th>Вход, знаков</th><th>Выход, знаков</th><th>Ток./с</th><th>Последний</th></tr></thead>
            <tbody>
              {rows.map((m) => (
                <tr key={`${m.model}|${m.local}|${m.profile}|${m.kind}`}>
                  <td><b>{m.title}</b> <span className="badge">{m.local ? "локальная" : "внешняя"}</span></td>
                  <td>{KIND[m.kind] ?? m.kind}</td>
                  <td>{m.documents}</td>
                  <td>{m.failed ? <span className="field-err">{m.failed}</span> : 0}</td>
                  <td>{m.truncated || m.length_hits ? <span className="field-err">{m.truncated} (обрывов {m.length_hits})</span> : 0}</td>
                  <td>{m.retries}</td>
                  <td>{m.avg_s !== null ? `${spanRu(m.avg_s)} / ${spanRu(m.median_s)}` : "—"}</td>
                  <td>{m.avg_input_chars?.toLocaleString("ru-RU") ?? "—"}</td>
                  <td>{m.avg_output_chars?.toLocaleString("ru-RU") ?? "—"}</td>
                  <td>{m.tokens_per_s ?? "—"}</td>
                  <td className="small">{m.last ? fmtDate(m.last) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
