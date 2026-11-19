import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ApiError, type MemoryAnswer, type MemoryEvent, type MemoryObservation, type MemoryThread, type RoomMemoryState } from "../api";
import { useToast } from "../components/Toast";
import { useFocusOnMount } from "../useInitialFocus";
import { KIND_LABEL, PERIODS, day, history, inPeriod, lanes, matchThread, periodFor, summarize, topicIdOf, type Period } from "../memoryView";
import "../memory.css";

type Tab = "overview" | "threads" | "outcomes" | "ask";
const TABS: [Tab, string][] = [["overview", "Обзор"], ["threads", "Темы"], ["outcomes", "Решения и задачи"], ["ask", "Вопрос к истории"]];
const GLYPH: Record<string, string> = { decision: "◆", task: "▲", question: "?", proposal: "○", mention: "●" };
const TONE: Record<string, string> = { attention: "Требует внимания", info: "К сведению", good: "Продвижение" };

/** «Эволюция обсуждений»: накопительная память комнаты — сквозные темы, их история по встречам, наблюдения, вопрос к истории. */
export default function RoomMemoryPage() {
  const { roomId = "" } = useParams();
  const [st, setSt] = useState<RoomMemoryState | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  const [pid, setPid] = useState("quarter");
  const [custom, setCustom] = useState<Period>({ from: null, to: null });
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [stateF, setStateF] = useState("");
  const [busy, setBusy] = useState(false);
  const [toastNode, toast] = useToast();

  const load = useCallback(async () => {
    try { setSt(await api.roomMemory.get(roomId)); setError(""); } catch (e) { setError((e as ApiError).message); }
  }, [roomId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {                                       // пока идёт обработка — тихо обновлять раз в 5 с
    if (st?.status !== "running") return;
    const t = window.setTimeout(load, 5000);
    return () => window.clearTimeout(t);
  }, [st, load]);

  const period = pid === "custom" ? custom : periodFor(pid);
  const sum = useMemo(() => (st ? summarize(st, period) : null), [st, period.from, period.to]);    // eslint-disable-line react-hooks/exhaustive-deps
  const threadsById = useMemo(() => new Map((st?.threads ?? []).map((t) => [t.id, t])), [st]);
  const evById = useMemo(() => new Map((st?.events ?? []).map((e) => [e.id, e])), [st]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    try { await fn(); if (ok) toast(ok); await load(); } catch (e) { toast((e as ApiError).message, "error"); } finally { setBusy(false); }
  };
  const openThread = (id: string) => { setSel(id); setTab("threads"); window.scrollTo({ top: 0, behavior: "smooth" }); };

  if (error) return <section><p><Link to="/history">← К истории</Link></p><div className="alert error" role="alert">{error}</div></section>;
  if (!st || !sum) return <section className="muted">Загрузка…</section>;
  const cov = st.coverage;

  return (
    <section className="mem">
      {toastNode}
      <p className="mem-back"><Link to="/history">← К истории</Link></p>
      <header className="mem-hd">
        <div className="mem-hd-text">
          <div className="mem-eyebrow">Эволюция обсуждений</div>
          <h1>{st.room.name}</h1>
          <p className="muted">Темы, к которым комната возвращается от встречи к встрече: как появлялись идеи, какие решения приняты и что осталось открытым.</p>
        </div>
        <div className="mem-tools">
          <span className={`badge ${st.status === "failed" ? "err" : st.status === "running" ? "" : "ok"}`}>{!st.enabled ? "Выключена" : st.status === "running" ? "Обновляется…" : st.status === "failed" ? "Ошибка" : "Готово"}</span>
          {st.updated_at && st.enabled && <span className="muted small">обновлено {new Date(st.updated_at).toLocaleString("ru-RU")}</span>}
          {st.can_manage && st.enabled && <button className="btn" disabled={busy || st.status === "running"} onClick={() => act(async () => {
            const r = await api.roomMemory.refresh(roomId); toast(r.maps_queued ? `Поставлено в очередь карт встреч: ${r.maps_queued}. История обновится по мере готовности.` : "История обновляется");
          })}>Обновить</button>}
        </div>
      </header>
      {st.status === "failed" && st.error && <div className="alert error small" role="alert">Последняя обработка не удалась: {st.error}. История показана по прежним данным.</div>}

      {!st.enabled && (
        <div className="card mem-off">
          <h2>Память комнаты выключена</h2>
          <p>Когда она включена, Peregovorka после каждой встречи строит карту разговора по стенограмме и связывает её темы с прежними встречами. Запись и распознавание речи при этом
            не включаются: учитываются только встречи, где стенограмма уже есть.</p>
          {st.can_manage
            ? <button className="btn primary" disabled={busy} onClick={() => act(async () => {
                const r = await api.roomMemory.settings(roomId, { mode: "on" });
                toast(r.maps_queued ? `Память включена. Карт поставлено в очередь: ${r.maps_queued}.` : "Память включена");
              })}>Включить память комнаты</button>
            : <p className="muted small">Включить её может руководитель комнаты или администратор.</p>}
          {st.threads.length > 0 && <p className="muted small">Ниже — история, накопленная раньше.</p>}
        </div>
      )}

      <div className="mem-period" role="group" aria-label="Период">
        {PERIODS.map(([id, label]) => <button key={id} className={`chip-btn${pid === id ? " on" : ""}`} aria-pressed={pid === id} onClick={() => setPid(id)}>{label}</button>)}
        <button className={`chip-btn${pid === "custom" ? " on" : ""}`} aria-pressed={pid === "custom"} onClick={() => setPid("custom")}>Свой период</button>
        {pid === "custom" && <span className="mem-dates">
          <input type="date" aria-label="С даты" value={custom.from ?? ""} onChange={(e) => setCustom({ ...custom, from: e.target.value || null })} />
          <span className="muted">—</span>
          <input type="date" aria-label="По дату" value={custom.to ?? ""} onChange={(e) => setCustom({ ...custom, to: e.target.value || null })} />
        </span>}
      </div>

      <div className="mem-kpi">
        <Kpi k="Темы в работе" v={sum.stats.active} s={`${sum.stats.fresh} новых · ${sum.stats.continued} продолжены`} />
        <Kpi k="Решения" v={sum.stats.decisions} s="с репликой-основанием" />
        <Kpi k="Поручения" v={sum.stats.tasks} s="ответственный и срок — как в стенограмме" />
        <Kpi k="Открытые вопросы" v={sum.stats.questions} s="заданы и не закрыты на встрече" />
        <Kpi k="Вернулись после решения" v={sum.stats.reopened} s={`без движения: ${sum.stats.dormant}`} />
        <Kpi k="Требуют внимания" v={sum.stats.attention} s="наблюдения ниже" tone={sum.stats.attention ? "warn" : undefined} />
      </div>
      <p className="muted small mem-cov">
        Учтено встреч: {cov.in_memory} из {cov.meetings}{cov.partial_view ? " (доступных вам)" : ""}.
        {cov.meetings - cov.with_transcript > 0 && ` Без стенограммы: ${cov.meetings - cov.with_transcript}.`}
        {cov.without_map > 0 && ` Со стенограммой, но ещё без карты: ${cov.without_map}${st.can_manage && st.enabled ? " — «Обновить» поставит их в очередь" : ""}.`}
      </p>

      {st.can_manage && st.enabled && (
        <details className="mem-settings">
          <summary>Настройки памяти</summary>
          <label className="mem-check"><input type="checkbox" checked={st.keep} disabled={busy}
                 onChange={(e) => act(() => api.roomMemory.settings(roomId, { keep: e.target.checked }), e.target.checked ? "История тем будет храниться дольше стенограммы (без цитат)" : "История тем удаляется вместе со стенограммой")} />
            Хранить историю тем дольше стенограммы — после очистки по сроку хранения события остаются без цитат, с пометкой «первоисточник удалён»</label>
          <button className="btn ghost mini" disabled={busy} onClick={() => act(() => api.roomMemory.settings(roomId, { mode: "off" }), "Память выключена: новые встречи не учитываются, накопленная история сохранена")}>Выключить память комнаты</button>
        </details>
      )}

      <div className="tabs" role="tablist" aria-label="Представление">
        {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>)}
      </div>

      {tab === "overview" && (
        <div className="mem-overview">
          <section className="card mem-obs" aria-label="Наблюдения">
            <div className="mem-card-h"><h2>Наблюдения</h2>{st.dismissed > 0 && <span className="muted small">скрыто: {st.dismissed}</span>}</div>
            <p className="muted small">Советчик подсказывает, решение принимает руководитель. Ничего не назначается и не меняется автоматически.</p>
            {st.observations.filter((o) => inPeriod(o.at, period)).length === 0 && <p className="muted">За выбранный период замечаний нет.</p>}
            {st.observations.filter((o) => inPeriod(o.at, period)).map((o) => (
              <Observation key={o.key} o={o} evById={evById} canManage={st.can_manage} busy={busy} onThread={openThread}
                           onFeedback={(s) => act(() => api.roomMemory.feedback(roomId, o.key, s), s === "dismissed" ? "Наблюдение скрыто: вернётся, только если появятся новые основания" : "Спасибо, отмечено")} />
            ))}
          </section>
          <section className="card mem-lanes" aria-label="Развитие тем">
            <div className="mem-card-h"><h2>Развитие тем</h2><span className="muted small">встречи периода слева направо</span></div>
            <Lanes st={st} sum={sum} onThread={openThread} />
          </section>
        </div>
      )}

      {tab === "threads" && (
        <div className="mem-split">
          <aside className="card mem-list" aria-label="Темы">
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти тему, решение, поручение" aria-label="Поиск по темам" />
            <select value={stateF} onChange={(e) => setStateF(e.target.value)} aria-label="Состояние">
              <option value="">Все состояния</option>
              {Object.entries(st.states).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <ThreadList threads={st.threads.filter((t) => matchThread(t, st.events, q, stateF)).sort((a, b) => b.last_at.localeCompare(a.last_at))} sel={sel} onSel={setSel} />
          </aside>
          <section className="card mem-detail">
            {sel && threadsById.get(sel)
              ? <ThreadDetail key={sel} t={threadsById.get(sel)!} st={st} busy={busy} roomId={roomId} act={act} onThread={setSel} />
              : <p className="muted">Выберите тему слева — здесь появится её история: когда возникла, кто предлагал, какие решения принимались и к чему возвращались.</p>}
          </section>
        </div>
      )}

      {tab === "outcomes" && <Outcomes st={st} events={sum.events} threadsById={threadsById} onThread={openThread} />}
      {tab === "ask" && <Ask roomId={roomId} threadsById={threadsById} onThread={openThread} />}
    </section>
  );
}

function Kpi({ k, v, s, tone }: { k: string; v: number; s: string; tone?: "warn" }) {
  return <div className={`mem-tile${tone ? ` ${tone}` : ""}${v === 0 ? " zero" : ""}`}><div className="k">{k}</div><div className="v">{v}</div><div className="s">{s}</div></div>;
}

function Observation({ o, evById, canManage, busy, onThread, onFeedback }: {
  o: MemoryObservation; evById: Map<string, MemoryEvent>; canManage: boolean; busy: boolean; onThread: (id: string) => void; onFeedback: (s: "useful" | "dismissed") => void;
}) {
  return (
    <article className={`mem-ob ${o.tone}`}>
      <div className="mem-ob-top"><span className={`mem-tone ${o.tone}`}>{TONE[o.tone]}</span><span className="muted small">уверенность: {o.confidence}</span></div>
      <h3><button className="linkish" onClick={() => onThread(o.thread)}>{o.title}</button></h3>
      <p className="why">{o.why}</p>
      <div className="mem-evidence">{o.evidence.map((id) => evById.get(id)).filter(Boolean).map((e) => (
        <Link key={e!.id} className="mem-ev-chip" to={`/history/${e!.meeting}`} title="Открыть встречу">{day(e!.at)} · {KIND_LABEL[e!.kind]}</Link>
      ))}</div>
      <p className="action"><b>Что можно сделать:</b> {o.action}</p>
      {canManage && <div className="mem-ob-btns">
        <button className="btn mini" disabled={busy || o.feedback === "useful"} onClick={() => onFeedback("useful")}>{o.feedback === "useful" ? "Отмечено полезным" : "Полезно"}</button>
        <button className="btn mini ghost" disabled={busy} onClick={() => onFeedback("dismissed")}>Скрыть</button>
      </div>}
    </article>
  );
}

function Lanes({ st, sum, onThread }: { st: RoomMemoryState; sum: ReturnType<typeof summarize>; onThread: (id: string) => void }) {
  const rows = lanes(sum.threads, sum.events, sum.meetings);
  if (!rows.length) return <p className="muted">В выбранном периоде обсуждений нет.</p>;
  return (
    <>
      <div className="mem-lane-wrap">
        {/* не <table>: на узком экране общие правила таблиц приложения превращают строки в карточки, а здесь нужна именно сетка «тема × встреча» */}
        <div className="mem-lane" style={{ ["--n" as string]: sum.meetings.length }} role="table" aria-label="Развитие тем по встречам">
          <div className="mem-lane-row head" role="row">
            <span role="columnheader">Тема</span>
            {sum.meetings.map((m) => <span key={m} role="columnheader"><Link to={`/history/${m}`} title="Открыть встречу">{day(st.meetings[m]?.started_at ?? "").slice(0, 5)}</Link></span>)}
          </div>
          {rows.map((r) => (
            <div key={r.thread.id} className="mem-lane-row" role="row">
              <span role="rowheader" className="mem-lane-title"><button className="linkish" onClick={() => onThread(r.thread.id)}>{r.thread.title}</button><span className={`mem-state s-${r.thread.state}`}>{r.thread.state_title}</span></span>
              {r.cells.map((c, i) => <span key={i} role="cell" className={`mem-cell${c ? ` k-${c}` : ""}`} title={c ? KIND_LABEL[c as keyof typeof KIND_LABEL] : ""}>{c ? GLYPH[c] : ""}</span>)}
            </div>
          ))}
        </div>
      </div>
      <p className="mem-legend muted small">◆ решение · ▲ поручение · ? вопрос · ○ предложение · ● обсуждали</p>
    </>
  );
}

function ThreadList({ threads, sel, onSel }: { threads: MemoryThread[]; sel: string | null; onSel: (id: string) => void }) {
  if (!threads.length) return <p className="muted">Ничего не найдено.</p>;
  return (
    <div className="mem-threads" role="listbox" aria-label="Список тем">
      {threads.map((t) => (
        <button key={t.id} role="option" aria-selected={sel === t.id} className={`mem-thread${sel === t.id ? " sel" : ""}`} onClick={() => onSel(t.id)}>
          <span className="name">{t.title}</span>
          <span className="meta">{t.meetings.length} встр. · {day(t.first_at)} — {day(t.last_at)}</span>
          <span className="tags">
            <span className={`mem-state s-${t.state}`}>{t.state_title}</span>
            {t.counts.decision > 0 && <span className="tag ok">решений {t.counts.decision}</span>}
            {t.counts.task > 0 && <span className="tag warn">поручений {t.counts.task}</span>}
            {t.counts.question > 0 && <span className="tag">вопросов {t.counts.question}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}

function ThreadDetail({ t, st, busy, roomId, act, onThread }: {
  t: MemoryThread; st: RoomMemoryState; busy: boolean; roomId: string; act: (fn: () => Promise<unknown>, ok?: string) => Promise<void>; onThread: (id: string) => void;
}) {
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ title: t.title, state: t.user_state ?? "", author: t.author ?? "", note: t.note ?? "" });
  const [into, setInto] = useState("");
  const focus = useFocusOnMount<HTMLInputElement>();
  const groups = history(t, st.events);
  return (
    <article className="mem-hist">
      <div className="mem-hist-top">
        <div>
          <span className={`mem-state s-${t.state}`}>{t.state_title}</span>
          <h2>{t.title}</h2>
          {t.title !== t.ai_title && <p className="muted small">Название изменено вручную. По анализу: «{t.ai_title}».</p>}
        </div>
        {st.can_manage && !edit && <button className="btn mini" onClick={() => setEdit(true)}>Править</button>}
      </div>
      <div className="mem-facts">
        <span>Впервые: <b>{day(t.first_at)}</b></span><span>Последнее обсуждение: <b>{day(t.last_at)}</b></span><span>Встреч: <b>{t.meetings.length}</b></span>
        <span>Автор идеи: <b>{t.author ?? "не установлен"}</b>{t.author && <span className="muted"> {t.author_is_user ? "(указано вручную)" : "(по первому предложению)"}</span>}</span>
      </div>
      {t.summary && <p className="mem-sum">{t.summary}</p>}
      {t.note && <p className="mem-note"><b>Заметка:</b> {t.note}</p>}

      {edit && (
        <div className="mem-edit">
          <label>Название<input ref={focus} value={f.title} maxLength={200} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
          <label>Состояние<select value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}>
            <option value="">Определять по обсуждениям</option>
            <option value="closed">Закрыта</option><option value="in_progress">Поручения в работе</option><option value="decided">Решение принято</option><option value="discussed">Обсуждается</option>
          </select></label>
          <label>Автор идеи<input value={f.author} maxLength={200} onChange={(e) => setF({ ...f, author: e.target.value })} placeholder="Пусто — по первому предложению" /></label>
          <label>Заметка<textarea rows={2} value={f.note} maxLength={1000} onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
          <div className="row">
            <button className="btn primary" disabled={busy} onClick={() => act(() => api.roomMemory.editThread(roomId, t.id, f), "Сохранено. Повторный анализ эту правку не изменит.").then(() => setEdit(false))}>Сохранить</button>
            <button className="btn ghost" onClick={() => setEdit(false)}>Отмена</button>
          </div>
          <div className="mem-merge">
            <label>Это та же тема, что…<select value={into} onChange={(e) => setInto(e.target.value)}>
              <option value="">— выберите —</option>
              {st.threads.filter((x) => x.id !== t.id).sort((a, b) => a.title.localeCompare(b.title, "ru")).map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
            </select></label>
            <button className="btn" disabled={busy || !into} onClick={() => act(() => api.roomMemory.merge(roomId, t.id, into), "Темы объединены").then(() => onThread(into))}>Объединить</button>
          </div>
        </div>
      )}

      <h3 className="mem-h3">История</h3>
      <ol className="mem-timeline">
        {groups.map((g) => (
          <li key={g.meeting}>
            <div className="mem-tl-head">
              <Link to={`/history/${g.meeting}`}>{day(g.at)}</Link>
              <span className="muted small">{g.events.some((e) => e.source_gone) ? "первоисточник удалён по сроку хранения" : ""}</span>
              {st.can_manage && groups.length > 1 && (
                <button className="btn mini ghost" disabled={busy} title="Обсуждение этой встречи относится к другой теме"
                        onClick={() => { const tid = topicIdOf(st.events, t.id, g.meeting); if (tid) void act(() => api.roomMemory.split(roomId, t.id, g.meeting, tid), "Встреча отделена в собственную тему"); }}>Отделить</button>
              )}
            </div>
            <ul>
              {g.events.map((e) => (
                <li key={e.id} className={`mem-ev k-${e.kind}`}>
                  <span className="mem-kind">{KIND_LABEL[e.kind]}</span>
                  <span className="mem-ev-text">{e.text}</span>
                  {e.kind === "task" && <span className="mem-ev-meta">Ответственный: {e.assignee ?? "не назван"} · Срок: {e.deadline ?? "не назван"}</span>}
                  {e.kind !== "mention" && e.speaker && <span className="mem-ev-meta">{e.speaker}</span>}
                  {e.quote && e.kind !== "mention" && <blockquote>«{e.quote}»</blockquote>}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </article>
  );
}

function Outcomes({ st, events, threadsById, onThread }: { st: RoomMemoryState; events: MemoryEvent[]; threadsById: Map<string, MemoryThread>; onThread: (id: string) => void }) {
  const groups: [string, string, MemoryEvent[]][] = [
    ["decision", "Принятые решения", events.filter((e) => e.kind === "decision")],
    ["task", "Поручения", events.filter((e) => e.kind === "task")],
    ["question", "Открытые вопросы", events.filter((e) => e.kind === "question")],
    ["proposal", "Предложения", events.filter((e) => e.kind === "proposal")],
  ];
  return (
    <div className="mem-outcomes">
      {groups.map(([k, title, list]) => (
        <section key={k} className="card">
          <div className="mem-card-h"><h2>{title}</h2><span className="cnt">{list.length}</span></div>
          {!list.length && <p className="muted small">За период нет.</p>}
          <ul className="mem-out-list">
            {list.slice().reverse().map((e) => (
              <li key={e.id}>
                <span className="mem-ev-text">{e.text}</span>
                {k === "task" && <span className="mem-ev-meta">Ответственный: {e.assignee ?? "не назван"} · Срок: {e.deadline ?? "не назван"}</span>}
                <span className="mem-ev-meta">
                  <Link to={`/history/${e.meeting}`}>{day(st.meetings[e.meeting]?.started_at ?? e.at)}</Link>{" · "}
                  <button className="linkish" onClick={() => onThread(e.thread)}>{threadsById.get(e.thread)?.title}</button>
                  {e.speaker ? ` · ${e.speaker}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Ask({ roomId, threadsById, onThread }: { roomId: string; threadsById: Map<string, MemoryThread>; onThread: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<MemoryAnswer | null>(null);
  const [err, setErr] = useState("");
  const focus = useFocusOnMount<HTMLInputElement>();
  const go = async () => {
    setBusy(true); setErr("");
    try { setRes(await api.roomMemory.ask(roomId, q)); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  const examples = ["Когда впервые предложили автоматизировать отчёты?", "Что решили на последней встрече?", "Какие поручения без ответственного?"];
  return (
    <section className="card mem-ask">
      <h2>Вопрос к истории комнаты</h2>
      <p className="muted small">Ответ строится только по событиям встреч, доступных вам; в языковую модель уходят отобранные формулировки, а не стенограммы целиком.</p>
      <div className="mem-ask-row">
        <input ref={focus} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && q.trim().length > 2) void go(); }} placeholder="Например: почему отказались от прежнего решения?" aria-label="Вопрос" maxLength={400} />
        <button className="btn primary" disabled={busy || q.trim().length < 3} onClick={() => void go()}>{busy ? "Ищу…" : "Спросить"}</button>
      </div>
      <div className="mem-examples">{examples.map((x) => <button key={x} className="chip-btn" onClick={() => setQ(x)}>{x}</button>)}</div>
      {err && <div className="alert error small" role="alert">{err}</div>}
      {res && (
        <div className="mem-answer">
          {res.answer && <div className="mem-answer-text"><span className={`badge ${res.basis === "ai" ? "" : "ok"}`}>{res.basis === "ai" ? "Ответ модели по событиям — сверяйте с источниками" : "Поиск"}</span><p>{res.answer}</p></div>}
          {res.note && <p className="muted small">{res.note}</p>}
          {res.events.length > 0 && <>
            <h3 className="mem-h3">Источники</h3>
            <ul className="mem-out-list">{res.events.map((e) => (
              <li key={e.id}>
                <span className="mem-ref">{e.ref}</span> <span className="mem-kind">{KIND_LABEL[e.kind]}</span> <span className="mem-ev-text">{e.text}</span>
                <span className="mem-ev-meta"><Link to={`/history/${e.meeting}`}>{day(e.at)}</Link> · <button className="linkish" onClick={() => onThread(e.thread)}>{threadsById.get(e.thread)?.title}</button></span>
              </li>
            ))}</ul>
          </>}
        </div>
      )}
    </section>
  );
}
