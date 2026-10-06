import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ApiError, type Meeting, type ProtocolItem, type Segment } from "../api";
import { formatTime, renderProtocol } from "../transcript";
import { fmt } from "./HistoryPage";

export default function MeetingPage() {
  const { meetingId = "" } = useParams();
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [protocols, setProtocols] = useState<ProtocolItem[]>([]);
  const [open, setOpen] = useState<ProtocolItem | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const loadProtocols = useCallback(async () => {
    const list = await api.protocols(meetingId).catch(() => [] as ProtocolItem[]);
    setProtocols(list);
    window.clearTimeout(timer.current);
    if (list.some((p) => p.status === "pending")) timer.current = window.setTimeout(loadProtocols, 3000); // ждём LLM
    return list;
  }, [meetingId]);

  useEffect(() => {
    api.meeting(meetingId).then(setMeeting).catch((e) => setError(e.message));
    void loadProtocols();
    (async () => {
      let after = 0; const all: Segment[] = [];
      for (;;) {
        const page = await api.transcript(meetingId, after);
        all.push(...page.segments);
        if (!page.has_more || page.segments.length === 0) break;
        after = Math.max(...page.segments.map((s) => s.id));
      }
      all.sort((a, b) => a.started_at.localeCompare(b.started_at) || a.id - b.id);
      setSegments(all);
    })().catch((e) => setError(e.message));
    return () => window.clearTimeout(timer.current);
  }, [meetingId, loadProtocols]);

  const text = useMemo(() => meeting
    ? renderProtocol(meeting.room_name, meeting.started_at, meeting.participants.map((p) => p.display_name), segments) : "", [meeting, segments]);

  const download = (content: string, name: string) => {
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
  };

  const makeSummary = async () => {
    setBusy(true); setError("");
    try { await api.createSummary(meetingId); await loadProtocols(); }
    catch (e) { setError((e as ApiError).message); }
    finally { setBusy(false); }
  };

  const show = async (p: ProtocolItem) => setOpen(await api.protocol(meetingId, p.id));

  if (error && !meeting) return <div className="alert error">{error}</div>;
  if (!meeting) return <div className="muted">Загрузка…</div>;
  return (
    <section>
      <p><Link to="/history">← К истории</Link></p>
      <h1>{meeting.room_name}</h1>
      <p className="muted">{fmt(meeting.started_at)} — {meeting.ended_at ? fmt(meeting.ended_at) : "идёт"} · Участвовали: {meeting.participants.map((p) => p.display_name).join(", ")}</p>
      {error && <div className="alert error">{error}</div>}
      <div className="row">
        <a className="btn" href={`/api/v1/meetings/${meetingId}/transcript.txt`}>Скачать стенограмму .txt</a>
        <button className="btn" onClick={() => download(text, `protocol-${meetingId.slice(0, 8)}.txt`)} disabled={!segments.length}>Скачать (с этой страницы)</button>
        {meeting.ended_at && <button className="btn primary" onClick={makeSummary} disabled={busy || !segments.length}>Создать краткий протокол (решения, поручения)</button>}
      </div>

      {protocols.length > 0 && (
        <div className="card" style={{ marginTop: 12, maxWidth: 900 }}>
          <h2>Краткие протоколы</h2>
          {protocols.map((p) => (
            <div key={p.id} className="row" style={{ padding: "4px 0" }}>
              <span>{fmt(p.created_at)}</span>
              <span className={`badge ${p.status === "failed" ? "warn" : ""}`}>{p.status === "pending" ? "создаётся…" : p.status === "ready" ? "готов" : "ошибка"}</span>
              {p.model && <span className="muted small">{p.model}</span>}
              {p.status === "ready" && <button className="btn mini" onClick={() => show(p)}>Открыть</button>}
              {p.status === "failed" && <span className="small" style={{ color: "var(--danger-text)" }}>{p.error}</span>}
            </div>
          ))}
          {open?.content && (
            <>
              <div className="proto card" style={{ marginTop: 8 }}>{open.content}</div>
              <div className="row" style={{ marginTop: 8 }}><button className="btn mini" onClick={() => download(open.content ?? "", `summary-${meetingId.slice(0, 8)}.txt`)}>Скачать .txt</button>
                <span className="muted small">Текст составлен по обезличенной стенограмме: метки вида [ФИО_1] — заменённые данные.</span></div>
            </>
          )}
        </div>
      )}

      <div className="card transcript-full">
        <h2>Стенограмма</h2>
        {segments.length === 0 && <p className="muted">Реплик нет.</p>}
        {segments.map((s) => (
          <p key={s.uid} className="utt"><span className="time">{formatTime(s.started_at)}</span><strong>{s.display_name}</strong><span>{s.text}</span></p>
        ))}
      </div>
    </section>
  );
}
