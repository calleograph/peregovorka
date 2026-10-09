import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, releaseOnUnload, type ApiError, type ExportFormat, type Meeting, type MeetingRecording, type ProtocolItem, type ProtocolKind, type Segment } from "../api";
import BoardViewer from "../board/BoardViewer";
import ChatPanel from "../components/ChatPanel";
import { Icon } from "../components/Icons";
import Menu from "../components/Menu";
import MeetingAdminActions from "../components/MeetingAdminActions";
import SendMaterialsDialog from "../components/SendMaterialsDialog";
import ProtocolDialog from "../components/ProtocolDialog";
import { docState, generationLine } from "../components/GenerationInfo";
import ProtocolViewer from "../components/ProtocolViewer";
import { formatTime, renderProtocol, transcriptText } from "../transcript";
import { useToast } from "../components/Toast";
import { bytes, copyText, downloadText, duration, fileBase, fmt } from "../util";

const FORMATS: [ExportFormat, string][] = [["docx", "Word (.docx)"], ["pdf", "PDF (.pdf)"], ["md", "Markdown (.md)"], ["txt", "Обычный текст (.txt)"]];
const KIND_TITLE: Record<string, string> = { protocol: "Протокол", summary: "Резюме" };

// Доступ участника к завершённой встрече действует, пока открыта её страница. Уход со страницы освобождает доступ;
// «задержка» нужна, чтобы повторная инициализация эффекта (StrictMode, быстрый возврат) не сняла доступ ошибочно.
const pendingRelease = new Map<string, number>();
function useReleaseOnLeave(meetingId: string) {
  useEffect(() => {
    window.clearTimeout(pendingRelease.get(meetingId));
    pendingRelease.delete(meetingId);
    const onHide = () => releaseOnUnload(meetingId);
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      pendingRelease.set(meetingId, window.setTimeout(() => { pendingRelease.delete(meetingId); releaseOnUnload(meetingId); }, 400));
    };
  }, [meetingId]);
}

export default function MeetingPage({ isAdmin }: { isAdmin: boolean }) {
  const { meetingId = "" } = useParams();
  const navigate = useNavigate();
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [protocols, setProtocols] = useState<ProtocolItem[]>([]);
  const [recordings, setRecordings] = useState<MeetingRecording[]>([]);
  const [sendOpen, setSendOpen] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [opened, setOpened] = useState<ProtocolItem | null>(null);
  const [dialog, setDialog] = useState<{ kind: ProtocolKind; instruction?: string } | null>(null);
  const [tab, setTab] = useState<"docs" | "transcript" | "chat" | "board" | "audio">("docs");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const prevPending = useRef<Set<string>>(new Set());
  useReleaseOnLeave(meetingId);

  const loadProtocols = useCallback(async () => {
    const list = await api.protocols(meetingId).catch(() => null);
    if (!list) return [];
    setProtocols(list);
    const pending = new Set(list.filter((p) => p.status === "pending").map((p) => p.id));
    for (const p of list) if (p.status === "ready" && prevPending.current.has(p.id)) setOpenId(p.id); // только что готовый — сразу показываем
    prevPending.current = pending;
    window.clearTimeout(timer.current);
    if (pending.size) timer.current = window.setTimeout(loadProtocols, 3000); // ждём LLM
    return list;
  }, [meetingId]);

  const loadMeeting = useCallback(() => api.meeting(meetingId).then(setMeeting).catch((e) => setError((e as ApiError).message)), [meetingId]);
  const loadRecordings = useCallback(() => { if (isAdmin) void api.meetingRecordings(meetingId).then(setRecordings).catch(() => setRecordings([])); }, [meetingId, isAdmin]);

  useEffect(() => {
    void loadMeeting();
    void loadProtocols().then((l) => { const first = l.find((p) => p.status === "ready"); if (first) setOpenId((cur) => cur ?? first.id); });
    loadRecordings();
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
    })().catch((e) => setError((e as ApiError).message));
    return () => window.clearTimeout(timer.current);
  }, [meetingId, loadMeeting, loadProtocols, loadRecordings]);

  useEffect(() => {
    if (!openId) { setOpened(null); return; }
    let cancelled = false;
    const list = protocols.find((p) => p.id === openId);
    if (list?.status !== "ready") { setOpened(list ?? null); return; }
    api.protocol(meetingId, openId).then((p) => { if (!cancelled) setOpened(p); }).catch((e) => { if (!cancelled) setError((e as ApiError).message); });
    return () => { cancelled = true; };
  }, [openId, meetingId, protocols]);

  const [toastNode, toast] = useToast();
  const text = useMemo(() => meeting
    ? renderProtocol(meeting.room_name, meeting.started_at, meeting.participants.map((p) => p.display_name), segments) : "", [meeting, segments]);

  const onStarted = async (id: string) => {
    setNotice("Документ формируется. Он появится в списке ниже и откроется автоматически.");
    await loadProtocols();
    setOpenId(id); setTab("docs");
  };
  const afterDelete = (id: string) => { setOpenId((cur) => (cur === id ? null : cur)); void loadProtocols(); void loadMeeting(); };

  if (error && !meeting) return <div className="alert error">{error}</div>;
  if (!meeting) return <div className="muted">Загрузка…</div>;
  const finished = !!meeting.ended_at;
  const chatCount = meeting.chat_messages ?? 0;
  const boardUsed = (meeting.whiteboard_shapes ?? 0) > 0;
  const canMake = finished && (segments.length > 0 || chatCount > 0 || boardUsed); // протокол строится по речи, чату и схеме вместе

  return (
    <section>
      <p><Link to="/history">← К истории</Link></p>
      <div className="row"><h1 style={{ margin: 0 }}>{meeting.room_name}</h1>{!finished && <span className="badge rec">идёт</span>}</div>
      <p className="muted">{fmt(meeting.started_at)} — {meeting.ended_at ? `${fmt(meeting.ended_at)} (${duration(meeting.started_at, meeting.ended_at)})` : "идёт"} · Участвовали: {meeting.participants.map((p) => p.display_name).join(", ") || "—"}
        {(chatCount > 0 || boardUsed) && <> · Материалы: {chatCount > 0 && `чат (${chatCount})`}{chatCount > 0 && boardUsed && ", "}{boardUsed && "общая доска (схема)"}</>}</p>
      {error && <div className="alert error" role="alert">{error}</div>}
      {notice && <div className="alert ok" role="status">{notice}</div>}
      {finished && <div className="alert info small">Пока эта страница открыта, вы можете формировать протоколы. После выхода со страницы доступ к материалам встречи закрывается (если администратор не разрешил иначе).</div>}

      <div className="row" style={{ margin: "10px 0" }}>
        <button className="btn primary" disabled={!canMake} onClick={() => setDialog({ kind: "protocol" })}
                title={!finished ? "Протокол формируется после завершения встречи" : !canMake ? "Нет ни реплик, ни чата, ни схемы" : "Окно с инструкцией для модели. Материалы: стенограмма, чат и схема с доски"}>Сформировать протокол</button>
        <button className="btn" disabled={!canMake} onClick={() => setDialog({ kind: "summary" })}>Сформировать резюме</button>
        <Menu label="Скачать стенограмму">
          {FORMATS.map(([f, l]) => <a key={f} href={api.transcriptExportUrl(meetingId, f)} download>{l}</a>)}
          <button onClick={() => downloadText(text, `${fileBase(meeting.room_name, meeting.started_at)}.txt`)} disabled={!segments.length}>Как на этой странице (.txt)</button>
        </Menu>
        {finished && (isAdmin || meeting.can_send_materials) && <button className="btn" onClick={() => setSendOpen(true)} title="Протокол, резюме и стенограмма — выбранным получателям по электронной почте">Отправить материалы…</button>}
        {isAdmin && <MeetingAdminActions meeting={meeting} onChanged={() => { void loadMeeting(); loadRecordings(); void loadProtocols(); }} onDeleted={() => navigate("/history")} />}
      </div>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "docs"} className={`tab ${tab === "docs" ? "active" : ""}`} onClick={() => setTab("docs")}>Протоколы и резюме{protocols.length ? ` (${protocols.length})` : ""}</button>
        <button role="tab" aria-selected={tab === "transcript"} className={`tab ${tab === "transcript" ? "active" : ""}`} onClick={() => setTab("transcript")}>Стенограмма ({segments.length})</button>
        {chatCount > 0 && <button role="tab" aria-selected={tab === "chat"} className={`tab ${tab === "chat" ? "active" : ""}`} onClick={() => setTab("chat")}>Чат ({chatCount})</button>}
        {boardUsed && <button role="tab" aria-selected={tab === "board"} className={`tab ${tab === "board" ? "active" : ""}`} onClick={() => setTab("board")}>Доска</button>}
        {isAdmin && <button role="tab" aria-selected={tab === "audio"} className={`tab ${tab === "audio" ? "active" : ""}`} onClick={() => setTab("audio")}>Записи ({recordings.length})</button>}
      </div>

      {tab === "docs" && (
        <div className="docs-layout">
          <div className="card docs-list">
            {protocols.length === 0 && <p className="muted">Документов пока нет. Нажмите «Сформировать протокол» — перед отправкой вы увидите и сможете изменить инструкцию.</p>}
            {protocols.map((p) => (
              <button key={p.id} className={`doc-item ${openId === p.id ? "active" : ""}`} onClick={() => setOpenId(p.id)}>
                <span><b>{p.title || KIND_TITLE[p.kind] || p.kind}</b> <span className="badge">{KIND_TITLE[p.kind] ?? p.kind}</span></span>
                <span className="muted small">{fmt(p.created_at)} · {docState(p).label}</span>
                {generationLine(p) && <span className="muted small">{generationLine(p)}</span>}
              </button>
            ))}
          </div>
          <div style={{ minWidth: 0 }}>
            {opened ? <ProtocolViewer key={opened.id} meetingId={meetingId} item={opened} isAdmin={isAdmin}
                onChanged={(p) => { setOpened(p); void loadProtocols(); }} onDeleted={afterDelete}
                onRegenerate={(p) => setDialog({ kind: (p.kind === "summary" ? "summary" : "protocol"), instruction: p.instruction ?? undefined })} />
              : <div className="card muted">Выберите документ слева.</div>}
          </div>
        </div>
      )}

      {tab === "transcript" && (
        <div className="card transcript-full" style={{ maxWidth: 1000 }}>
          <div className="row" style={{ marginBottom: 8 }}>
            <button className="btn mini" disabled={!segments.length} title="Скопировать всю стенограмму обычным текстом: время, имя, реплика"
                    onClick={async () => { if (await copyText(transcriptText(segments))) toast("Стенограмма скопирована"); else toast("Не удалось скопировать — выделите текст вручную", "error"); }}>
              <Icon name="copy" size={14} /> Копировать</button>
            <span className="muted small">{segments.length ? `${segments.length} реплик` : ""}</span>
          </div>
          {segments.length === 0 && <p className="muted">Реплик нет.</p>}
          {segments.map((s) => <p key={s.uid} className="utt"><span className="time">{formatTime(s.started_at)}</span><strong>{s.display_name}</strong><span>{s.text}</span></p>)}
        </div>
      )}

      {tab === "chat" && (
        <div className="card chat-history" style={{ maxWidth: 1000 }}>
          <div className="row"><a className="btn mini" href={api.chatTextUrl(meetingId)} download>Скачать чат (.txt)</a>
            <span className="muted small">Чат — отдельный источник для протокола: ссылки, адреса, имена серверов и номера задач передаются модели дословно.</span></div>
          <ChatPanel meetingId={meetingId} readOnly visible />
        </div>
      )}

      {tab === "board" && <BoardViewer meetingId={meetingId} fileBase={fileBase(meeting.room_name, meeting.started_at) + "_схема"} />}

      {tab === "audio" && isAdmin && (
        <div className="card">
          <p className="muted">Аудиозаписи участников. Скачивание доступно только администраторам и записывается в аудит. Статус выгрузки показывает, сохранена ли копия во внешнем хранилище записей.</p>
          <table className="table"><thead><tr><th>Участник</th><th>Файл</th><th>Длительность</th><th>Размер</th><th>Выгрузка</th><th /></tr></thead><tbody>
            {recordings.length === 0 && <tr><td colSpan={6} className="muted">Записей нет.</td></tr>}
            {recordings.map((r) => (
              <tr key={r.id}><td>{r.identity}</td><td>{r.name}</td><td>{r.duration_s ? `${Math.floor(r.duration_s / 60)}:${String(r.duration_s % 60).padStart(2, "0")}` : "—"}</td><td>{bytes(r.size_bytes)}</td>
                <td>{r.export_status}{r.export_error && <span className="small" style={{ color: "var(--danger-text)" }}> {r.export_error}</span>}</td>
                <td>{r.file_state === "missing" ? <span className="badge warn" title="Файл удалён из хранилища (обнаружено при сверке)">файл удалён</span> : <a href={api.recordingUrl(meetingId, r.id)} download>Скачать</a>}</td></tr>))}
          </tbody></table>
        </div>
      )}

      {sendOpen && <SendMaterialsDialog meetingId={meetingId} onClose={() => setSendOpen(false)} />}
      {dialog && <ProtocolDialog meetingId={meetingId} kind={dialog.kind} isAdmin={isAdmin} initialInstruction={dialog.instruction} onClose={() => setDialog(null)} onStarted={onStarted} />}
      {toastNode}
    </section>
  );
}
