import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type LiveQuestion, type LiveQuestionFeed } from "../api";
import type { LiveBus } from "../liveSocket";
import { formatTime } from "../transcript";

type Filter = "new" | "done" | "hidden" | "all";
const FILTERS: { id: Filter; label: string }[] = [{ id: "new", label: "Новые" }, { id: "done", label: "Отвеченные" }, { id: "hidden", label: "Скрытые" }, { id: "all", label: "Все" }];

/**
 * Вопросы зрителей эфира для ведущих (комната трансляции). Новые — сверху очереди в порядке поступления: ведущий берёт следующий и отмечает
 * «Ответил» одним нажатием, не отвлекаясь от показа. Лента обновляется по событию комнаты (текста в событии нет — запрос с проверкой прав).
 * Почта автора — только руководителям и организатору, как контакт.
 */
export default function LiveQuestionsPanel({ meetingId, bus, onCount }: { meetingId: string; bus?: LiveBus; onCount?: (n: number) => void }) {
  const [feed, setFeed] = useState<LiveQuestionFeed | null>(null);
  const [filter, setFilter] = useState<Filter>("new");
  const [err, setErr] = useState("");
  const timer = useRef(0);
  const cb = useRef(onCount);
  cb.current = onCount;

  const load = useCallback(() => api.liveQuestions(meetingId).then((f) => { setFeed(f); setErr(""); cb.current?.(f.counts.new); })
    .catch((e) => setErr((e as ApiError).message || "Вопросы не загрузились")), [meetingId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!bus) return;
    // при наплыве вопросов не дёргаем сервер на каждый: одна загрузка на пачку событий
    const off = bus.on((e) => { if (e.type === "live_question") { window.clearTimeout(timer.current); timer.current = window.setTimeout(() => void load(), 400); } });
    const off2 = bus.onResync(() => void load());
    return () => { off(); off2(); window.clearTimeout(timer.current); };
  }, [bus, load]);

  const set = async (q: LiveQuestion, status: LiveQuestion["status"]) => {
    setFeed((f) => f && ({ ...f, items: f.items.map((x) => (x.id === q.id ? { ...x, status } : x)) }));       // сразу в интерфейсе; сервер подтвердит событием
    try { await api.liveQuestionSet(meetingId, q.id, status); } catch (e) { setErr((e as ApiError).message); }
    void load();
  };

  const items = (feed?.items ?? []).filter((q) => filter === "all" || q.status === filter);
  const shown = filter === "new" ? items : items.slice().reverse();
  return (
    <div className="lq">
      <div className="lq-filters" role="tablist" aria-label="Какие вопросы показать">
        {FILTERS.map((f) => {
          const n = f.id === "all" ? feed?.items.length ?? 0 : feed?.counts[f.id] ?? 0;
          return <button key={f.id} type="button" role="tab" aria-selected={filter === f.id} className={`chip ${filter === f.id ? "active" : ""}`} onClick={() => setFilter(f.id)}>
            {f.label}{n ? <span className="lq-n">{n}</span> : null}</button>;
        })}
      </div>
      {err && <div className="alert error small" role="alert">{err}</div>}
      {feed && !feed.items.length && <p className="muted small lq-empty">Вопросов пока нет. Зрители задают их на странице просмотра эфира — здесь они появятся сразу.</p>}
      {feed && feed.items.length > 0 && !shown.length && <p className="muted small lq-empty">{filter === "new" ? "Новых вопросов нет." : "Здесь пусто."}</p>}
      <ol className="lq-list">
        {shown.map((q) => (
          <li key={q.id} className={`lq-item ${q.status}`}>
            <div className="lq-meta"><b>{q.author}</b><span className="muted small">{formatTime(q.at)}</span>
              {q.email && <span className="muted small lq-mail" title={q.materials_consent ? "Согласен получать материалы встречи" : "Контакт; согласия на рассылку нет"}>{q.email}</span>}</div>
            <p className="lq-text">{q.text}</p>
            {feed?.can_moderate && (
              <div className="lq-act">
                {q.status !== "done" && <button type="button" className="btn mini primary" onClick={() => void set(q, "done")}>Ответил</button>}
                {q.status !== "hidden" && <button type="button" className="btn mini" onClick={() => void set(q, "hidden")}>Скрыть</button>}
                {q.status !== "new" && <button type="button" className="btn mini" onClick={() => void set(q, "new")}>Вернуть в новые</button>}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
