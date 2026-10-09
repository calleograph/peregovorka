import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ApiError, type EffectiveModel, type EffectiveModels, type LlmChoices, type LlmDiagnose, type ModelStat } from "../../api";
import { Modal } from "../../components/Dialogs";
import { fmtDate } from "./common";
import { spanRu } from "../../util";
import ApiProfilesAdmin from "./ApiProfilesAdmin";
import LocalLlmPanel from "./LocalLlmPanel";
import SettingsForm from "./SettingsForm";
import { llmApiFields, llmTaskFields, structuredFields } from "./fields";

const TABS = [["assign", "Назначения"], ["local", "Локальные модели"], ["external", "Внешние API"], ["gen", "Параметры генерации"], ["stats", "Статистика"], ["diag", "Диагностика"]] as const;
type Tab = (typeof TABS)[number][0];
const TASKS = [["protocol", "Протокол"], ["summary", "Резюме"], ["map", "Карта разговора"]] as const;
type TaskKey = (typeof TASKS)[number][0];
const API_TYPE: Record<string, string> = { local: "локальная", openai: "OpenAI", anthropic: "Anthropic", openai_compatible: "OpenAI-совместимый", custom: "свой OpenAI-подобный" };

/**
 * Администрирование → Языковая модель (LLM): вкладки вместо одной длинной страницы. Главная — «Назначения»: какая модель для какой задачи.
 * Подключения к внешним API — единый каталог (любой профиль — обычный профиль); параметры задач, статистика и диагностика — отдельно.
 */
export default function LlmAdmin() {
  const [tab, setTab] = useState<Tab>(() => { try { const t = sessionStorage.getItem("llmTab"); return TABS.some(([k]) => k === t) ? (t as Tab) : "assign"; } catch { return "assign"; } });
  const pick = (t: Tab) => { setTab(t); try { sessionStorage.setItem("llmTab", t); } catch { /* не запомнится */ } };
  return (
    <section className="llm-admin">
      <h2 style={{ marginBottom: 6 }}>Языковая модель (LLM)</h2>
      <div className="tabs" role="tablist" aria-label="Разделы языковой модели">
        {TABS.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => pick(k)}>{label}</button>)}
      </div>
      {tab === "assign" && <Assignments onGo={pick} />}
      {tab === "local" && <LocalLlmPanel />}
      {tab === "external" && <ApiProfilesAdmin key="llm-profiles" kind="llm" fields={llmApiFields} />}
      {tab === "gen" && <Generation />}
      {tab === "stats" && <Stats />}
      {tab === "diag" && <Diagnostics />}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ назначения
function stateBadge(m: EffectiveModel) {
  return !m.enabled ? <span className="badge">не настроено</span> : m.ready ? <span className="badge ok">готова</span> : <span className="badge warn" title={m.problem ?? ""}>{m.problem ?? "не готова"}</span>;
}

function Assignments({ onGo }: { onGo: (t: Tab) => void }) {
  const [eff, setEff] = useState<EffectiveModels | null>(null);
  const [ch, setCh] = useState<LlmChoices | null>(null);
  const [edit, setEdit] = useState<TaskKey | null>(null);
  const [src, setSrc] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try { const [e, c] = await Promise.all([api.admin.effectiveModels(), api.admin.llmChoices()]); setEff(e); setCh(c); setErr(""); } catch (x) { setErr((x as ApiError).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <div className="stack">
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="card">
        <h3 style={{ margin: 0 }}>Какая модель для какой задачи</h3>
        <details className="tech-details"><summary>Как определяется модель</summary>
          <p className="muted small" style={{ margin: "6px 0" }}>Сильнее всего разовый выбор в окне «Сформировать», затем настройка встречи, затем переговорки; если их нет — значение из этой таблицы.</p></details>
        {!eff || !ch ? <div className="muted">Загрузка…</div> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>Задача</th><th>Модель</th><th>Тип</th><th>Состояние</th><th /></tr></thead>
              <tbody>
                {TASKS.map(([k, label]) => {
                  const m = eff[k];
                  const own = k !== "map" ? eff.rooms_with_own_model[k] : 0;
                  const t = ch.tasks[k];
                  return (
                    <tr key={k}>
                      <th style={{ textAlign: "left" }}>{label}{t?.same_as_protocol && k !== "protocol" && <div className="muted small">как протокол</div>}</th>
                      <td>{m.enabled ? <><b>{m.name}</b>{m.model && !m.local ? <div className="muted small">{m.model}</div> : null}</> : <span className="muted">—</span>}</td>
                      <td>{m.enabled ? (m.local ? "Локальная" : `Внешняя · ${API_TYPE[m.api_type ?? ""] ?? m.api_type}`) : "—"}</td>
                      <td>{stateBadge(m)}{src && <div className="muted small">Система{own ? ` · в ${own} перегов. своя` : ""}</div>}</td>
                      <td><button className="btn mini" onClick={() => setEdit(k)}>Изменить</button></td>
                    </tr>);
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="row small"><button className="btn mini ghost" onClick={() => setSrc(!src)}>{src ? "Скрыть, почему выбрана модель" : "Почему выбрана эта модель"}</button>
          <span className="muted">Подключить новый API — вкладка «<a href="#llm-ext" onClick={(e) => { e.preventDefault(); onGo("external"); }}>Внешние API</a>».</span></div>
      </div>
      {ch && <FallbackPolicy value={ch.on_missing} onSaved={load} />}
      {edit && eff && ch && <AssignDialog task={edit} ch={ch} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); void load(); }} />}
    </div>
  );
}

function FallbackPolicy({ value, onSaved }: { value: "system" | "unavailable"; onSaved: () => void }) {
  const [err, setErr] = useState("");
  return (
    <div className="card small">
      <label>Если выбранная для переговорки или встречи модель недоступна (удалена, отключена)
        <select value={value} onChange={async (e) => { try { await api.admin.saveSettings("llm", { on_missing: e.target.value }); onSaved(); } catch (x) { setErr((x as ApiError).message); } }}>
          <option value="system">Использовать системное назначение задачи (с пометкой)</option>
          <option value="unavailable">Не формировать: показать «модель недоступна»</option>
        </select></label>
      <span className="help">Разовый выбор модели при формировании документа молча не заменяется.</span>
      {err && <div className="alert error">{err}</div>}
    </div>
  );
}

function AssignDialog({ task, ch, onClose, onSaved }: { task: TaskKey; ch: LlmChoices; onClose: () => void; onSaved: () => void }) {
  const cur = ch.tasks[task];
  const initial = cur.same_as_protocol && task !== "protocol" ? "same" : cur.mode === "local" ? "local" : cur.mode === "off" ? "off" : `ext:${cur.profile_id ?? ""}`;
  const [pick, setPick] = useState(initial);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const label = TASKS.find(([k]) => k === task)![1];
  const save = async () => {
    setBusy(true); setErr("");
    const mode = pick === "same" ? "same" : pick === "local" ? "local" : pick === "off" ? "off" : "external";
    const pid = pick.startsWith("ext:") ? pick.slice(4) || null : null;
    const patch: Record<string, unknown> = task === "protocol" ? { provider: mode, protocol_profile: pid } : { [`${task}_provider`]: mode, [`${task}_profile`]: pid };
    try { await api.admin.saveSettings("llm", patch as never); onSaved(); } catch (x) { setErr((x as ApiError).message); setBusy(false); }
  };
  const opt = (value: string, title: string, sub?: string, disabled = false) => (
    <label key={value} className="check"><input type="radio" name="assign" checked={pick === value} disabled={disabled} onChange={() => setPick(value)} />
      <span className="check-body">{title}{sub && <span className="help">{sub}</span>}</span></label>);
  return (
    <Modal title={`Модель для задачи: ${label}`} onClose={onClose}>
      <div className="stack">
        {task !== "protocol" && opt("same", "Как для протокола")}
        {ch.local.map((m) => opt("local", `${m.title} — локальная`, m.installed ? "Данные остаются на сервере" : "Файл модели не загружен — загрузите на вкладке «Локальные модели»", !m.installed))}
        {ch.external.map((p) => opt(`ext:${p.id}`, p.name, `${p.model || "модель не указана"}${p.host ? ` · ${p.host}` : ""}`))}
        {ch.external.length === 0 && <div className="muted small">Внешних API пока нет — добавьте подключение на вкладке «Внешние API».</div>}
        {opt("off", "Не использовать (задача отключена)")}
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row"><button className="btn primary" disabled={busy} onClick={() => void save()}>Сохранить</button><button className="btn ghost" onClick={onClose}>Отмена</button></div>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ параметры генерации
function Generation() {
  const [eff, setEff] = useState<EffectiveModels | null>(null);
  useEffect(() => { void api.admin.effectiveModels().then(setEff).catch(() => undefined); }, []);
  return (
    <div className="stack">
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Фактические пределы ответа</h3>
        <details className="tech-details"><summary>Как считается предел</summary>
          <p className="muted small" style={{ margin: "6px 0" }}>Предел — меньшее из трёх значений: потолок задачи (ниже), предел самого подключения и свободное окно контекста модели.</p></details>
        {!eff ? <div className="muted">Загрузка…</div> : (
          <table className="table compact"><tbody>
            {TASKS.map(([k, label]) => (
              <tr key={k}><th style={{ textAlign: "left" }}>{label}</th>
                <td>{eff[k].enabled && eff[k].max_output_tokens ? <><b>{eff[k].max_output_tokens}</b> токенов</> : <span className="muted">—</span>}</td>
                <td className="muted small">{eff[k].enabled ? (eff[k].max_output_note || "контекст модели учтён") : "задача не настроена"}</td></tr>))}
          </tbody></table>
        )}
      </div>
      <SettingsForm key="llm-task" group="llm" title="Параметры задач (протокол, резюме, карта)" fields={llmTaskFields}
        intro="Настройки задачи: потолок длины ответа для протокола, резюме и карты. Таймаут, температура, окно контекста и возможности — это параметры подключения: они в карточке подключения на вкладке «Внешние API»." />
      <SettingsForm key="protocol-mode" group="protocol" title="Структурный вывод и повторы" fields={structuredFields}
        intro="Режим структурного вывода: узкие запросы, JSON по схеме, таблицы и оформление собирает система. Оборванный по длине ответ повторяется по меньшим частям автоматически." />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ статистика
const KIND: Record<string, string> = { protocol: "протокол", summary: "резюме", map: "карта разговора" };

function Stats() {
  const [rows, setRows] = useState<ModelStat[] | null>(null);
  const [total, setTotal] = useState(0);
  const [hidden, setHidden] = useState(0);
  const [f, setF] = useState({ days: 0, kind: "", where: "", archived: false });
  const [err, setErr] = useState("");
  useEffect(() => { void api.admin.modelStats(f).then((r) => { setRows(r.models); setTotal(r.documents); setHidden(r.archived_hidden); setErr(""); }).catch((e) => setErr((e as ApiError).message)); }, [f]);
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Статистика моделей</h3>
      <div className="row small" style={{ flexWrap: "wrap", gap: 10 }}>
        <label>Период<select value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })}><option value={0}>всё время</option><option value={7}>7 дней</option><option value={30}>30 дней</option><option value={90}>90 дней</option></select></label>
        <label>Задача<select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="">все</option><option value="protocol">протокол</option><option value="summary">резюме</option><option value="map">карта разговора</option></select></label>
        <label>Модель<select value={f.where} onChange={(e) => setF({ ...f, where: e.target.value })}><option value="">любая</option><option value="local">локальная</option><option value="external">внешняя</option></select></label>
        <label className="check"><input type="checkbox" checked={f.archived} onChange={(e) => setF({ ...f, archived: e.target.checked })} /> Показывать устаревшие</label>
      </div>
      {err && <div className="alert error">{err}</div>}
      {rows && rows.length === 0 && <div className="muted">Документов за выбранный период нет.</div>}
      {hidden > 0 && <div className="muted small">Скрыто устаревших моделей: {hidden} (их история сохранена — «Показывать устаревшие»).</div>}
      {rows && rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr><th>Модель</th><th>Задача</th><th>Документов</th><th>Ошибок</th><th>Оборвано</th><th>Повторов</th><th>Время: среднее / медиана</th><th>Вход, знаков</th><th>Выход, знаков</th><th>Токенов/с</th><th>Последняя</th></tr></thead>
            <tbody>
              {rows.map((m) => (
                <tr key={`${m.model}|${m.local}|${m.profile}|${m.kind}`} className={m.archived ? "muted" : ""}>
                  <td><b>{m.title}</b> <span className="badge">{m.local ? "локальная" : "внешняя"}</span>{m.archived && <span className="badge warn"> архивная модель</span>}</td>
                  <td>{KIND[m.kind] ?? m.kind}</td><td>{m.documents}</td><td>{m.failed ? <span className="field-err">{m.failed}</span> : 0}</td>
                  <td>{m.truncated || m.length_hits ? <span className="field-err">{m.truncated} (обрывов {m.length_hits})</span> : 0}</td><td>{m.retries}</td>
                  <td>{m.avg_s !== null ? `${spanRu(m.avg_s)} / ${spanRu(m.median_s)}` : "—"}</td>
                  <td>{m.avg_input_chars?.toLocaleString("ru-RU") ?? "—"}</td><td>{m.avg_output_chars?.toLocaleString("ru-RU") ?? "—"}</td><td>{m.tokens_per_s ?? "—"}</td>
                  <td className="small">{m.last ? fmtDate(m.last) : "—"}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">Учтено документов и карт: {total || "—"}. Чем их больше, тем точнее прогноз времени в окне «Сформировать».</p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ диагностика
function Diagnostics() {
  const [ch, setCh] = useState<LlmChoices | null>(null);
  const [target, setTarget] = useState("task:protocol");
  const [res, setRes] = useState<LlmDiagnose | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { void api.admin.llmChoices().then(setCh).catch(() => undefined); }, []);
  const targets = useMemo(() => [
    ...TASKS.map(([k, l]) => [`task:${k}`, `Назначение: ${l}`] as const), ["local", "Локальная модель"] as const, ...(ch?.external ?? []).map((p) => [`profile:${p.id}`, `Подключение: ${p.name}`] as const),
  ], [ch]);
  const run = async () => { setBusy(true); setErr(""); setRes(null); try { setRes(await api.admin.llmDiagnose(target)); } catch (x) { setErr((x as ApiError).message); } setBusy(false); };
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Диагностика</h3>
      <div className="row">
        <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Что проверить">{targets.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <button className="btn primary" onClick={() => void run()} disabled={busy}>{busy ? "Проверка…" : "Проверить"}</button>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {res && (
        <div className="stack">
          <table className="table compact"><tbody>
            <tr><td>Будет вызван</td><td><b>{res.config.model || "—"}</b> · {res.config.provider === "local" ? "локальная" : res.config.provider === "external" ? `внешняя (${API_TYPE[res.config.type] ?? res.config.type})` : "отключена"}</td></tr>
            <tr><td>Адрес</td><td>{res.config.host || "—"}</td></tr>
            <tr><td>Предел ответа / контекст</td><td>{res.config.max_output_tokens} токенов · {res.config.context_window ? `${res.config.context_window} токенов` : "контекст модели не указан"}</td></tr>
            <tr><td>Температура</td><td>{String(res.config.temperature)}</td></tr>
            <tr><td>Возможности</td><td>системное сообщение: {res.config.capabilities.system ? "да" : "нет"} · строгий JSON: {res.config.capabilities.json ? "да" : "нет"} · temperature: {res.config.capabilities.temperature ? "передаётся" : "не передаётся"}</td></tr>
            <tr><td>Таймаут</td><td>{res.config.timeout_s} с</td></tr>
          </tbody></table>
          {res.tests.map((t) => <div key={t.name} className={`alert ${t.ok ? "ok" : "error"} small`} role="status"><b>{t.name}:</b> {t.ok ? "✓ " : "✗ "}{t.message} ({t.ms} мс)</div>)}
        </div>
      )}
      <p className="muted small">Ключи API в ответе диагностики не возвращаются.</p>
    </div>
  );
}
