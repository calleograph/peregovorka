import { playChatSound } from "../chatSound";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";
import { autoGrow } from "../autoGrow";
import { fileTypeLabel, formatSize, pastedName } from "../attachments";
import { Icon } from "./Icons";
import { api, type ApiError, type ChatAttachment, type ChatMessage } from "../api";
import type { LiveBus } from "../liveSocket";
import { linkify } from "../linkify";
import { formatTime } from "../transcript";
import { copyText } from "../util";
import { TypingSender, TypingTracker, typingText } from "../typing";

const MAX_LEN = 4000;

/** Текст сообщения: ссылки кликабельны и всегда открываются в НОВОЙ вкладке (вкладка встречи не должна терять соединение). */
export function MessageText({ text }: { text: string }) {
  return (
    <span className="chat-text">
      {linkify(text).map((c, i) => c.type === "link"
        ? <a key={i} href={c.href} target="_blank" rel="noopener noreferrer nofollow">{c.text}</a>
        : <span key={i}>{c.text}</span>)}
    </span>
  );
}

/** Файл вложения скачивается с заголовками сессии (так он доступен и гостю), а в браузер отдаётся через object URL. */
async function saveBlob(blob: Blob, name: string): Promise<void> {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.rel = "noopener";
  document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function ImageAttachment({ meetingId, a }: { meetingId: string; a: ChatAttachment }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(!!a.missing);
  useEffect(() => {
    if (a.missing) return;
    let alive = true; let made = "";
    api.attachmentBlob(meetingId, a.id).then((b) => { if (alive) { made = URL.createObjectURL(b); setUrl(made); } }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [meetingId, a.id]);
  if (failed) return <div className="chat-file broken"><Icon name="file" size={18} /><span className="chat-file-name">{a.name}</span><span className="muted small">{a.missing ? "файл удалён из хранилища" : "не удалось загрузить"}</span></div>;
  return (
    <a className="chat-image" href={url || undefined} target="_blank" rel="noopener noreferrer" title={`${a.name} · ${formatSize(a.size)} — открыть оригинал`}
       onClick={(e) => { if (!url) e.preventDefault(); }}>
      {url ? <img src={url} alt={a.name} loading="lazy" /> : <span className="chat-image-wait muted small">Загрузка…</span>}
    </a>
  );
}

function FileAttachment({ meetingId, a }: { meetingId: string; a: ChatAttachment }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (a.missing) return <div className="chat-file broken"><Icon name="file" size={20} /><div className="chat-file-info"><span className="chat-file-name" title={a.name}>{a.name}</span><span className="muted small">файл удалён из хранилища</span></div></div>;
  const download = async () => {
    setBusy(true); setErr("");
    try { await saveBlob(await api.attachmentBlob(meetingId, a.id, true), a.name); }
    catch (e) { setErr((e as ApiError).message || "Не удалось скачать"); }
    setBusy(false);
  };
  return (
    <div className="chat-file">
      <Icon name="file" size={20} />
      <div className="chat-file-info">
        <span className="chat-file-name" title={a.name}>{a.name}</span>
        <span className="muted small">{fileTypeLabel(a.name)} · {formatSize(a.size)}{err && <span className="err"> · {err}</span>}</span>
      </div>
      <button type="button" className="btn mini ghost" disabled={busy} onClick={() => void download()} title="Скачать файл">{busy ? "…" : "Скачать"}</button>
    </div>
  );
}

function Message({ m, mine, meetingId }: { m: ChatMessage; mine: boolean; meetingId: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { if (await copyText(m.text)) { setCopied(true); window.setTimeout(() => setCopied(false), 1500); } };
  return (
    <div className={`chat-msg ${mine ? "mine" : ""} ${m.author_type === "guest" ? "guest" : ""}`}>
      <div className="chat-meta">
        <strong>{m.author_name}</strong>
        <time dateTime={m.created_at} title={new Date(m.created_at).toLocaleString("ru-RU")}>{formatTime(m.created_at)}</time>
        <button type="button" className="chat-copy" onClick={copy} title="Копировать текст сообщения" aria-label="Копировать сообщение">{copied ? "Скопировано" : "Копировать"}</button>
      </div>
      {m.text && <MessageText text={m.text} />}
      {!!m.attachments?.length && (
        <div className="chat-atts">
          {m.attachments.map((a) => a.kind === "image" ? <ImageAttachment key={a.id} meetingId={meetingId} a={a} /> : <FileAttachment key={a.id} meetingId={meetingId} a={a} />)}
        </div>
      )}
    </div>
  );
}

/** Файл, выбранный для отправки: загружается сразу (чтобы «Отправить» было мгновенным), до отправки его можно убрать. */
interface Pending { key: string; file: File; preview: string; state: "uploading" | "ready" | "error"; att?: ChatAttachment; error?: string }

interface Props {
  meetingId: string;
  /** Поток событий комнаты; без него (история встречи) панель только читает. */
  bus?: LiveBus;
  readOnly?: boolean;
  /** Чат сейчас виден пользователю (вкладка открыта) — иначе новые сообщения считаются непрочитанными. */
  visible?: boolean;
  /** Имя текущего участника для выделения своих сообщений. */
  selfName?: string;
  onUnread?: (n: number) => void;
  /** Вложения разрешены (кнопка «Прикрепить», перетаскивание, вставка из буфера). */
  canAttach?: boolean;
}

/** Компактный рабочий чат встречи: сообщения, время и автор, ссылки, копирование, история. Не мессенджер: без реакций; вложения — файлы и картинки. */
export default function ChatPanel({ meetingId, bus, readOnly = false, visible = true, selfName, onUnread, canAttach = true }: Props) {
  const [items, setItems] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const pendingRef = useRef<Pending[]>([]);
  pendingRef.current = pending;
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stickRef = useRef(true);
  const keepRef = useRef<{ h: number; t: number } | null>(null);
  const unread = useRef(0);
  const itemsRef = useRef<ChatMessage[]>([]);
  itemsRef.current = items;
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const cb = useRef({ onUnread });
  cb.current = { onUnread };

  // «Печатает…»: отправляем «начал/закончил» с пульсом раз в несколько секунд, принимаем чужие и гасим по сроку; в историю чата это не попадает
  const sender = useRef<TypingSender | null>(null);
  const tracker = useRef(new TypingTracker());
  const [typers, setTypers] = useState<string[]>([]);
  useEffect(() => {
    if (!bus || readOnly) { sender.current = null; return; }
    const s = new TypingSender((t) => { void api.typing(meetingId, t).catch(() => undefined); });
    sender.current = s;
    return () => { s.stop(); sender.current = null; };
  }, [bus, readOnly, meetingId]);
  useEffect(() => {
    if (!bus) return;
    const off = bus.on((e) => { if (e.type === "chat_typing") { tracker.current.event(e.id, e.name, e.typing); setTypers(tracker.current.names(Date.now(), selfName)); } });
    const tick = window.setInterval(() => { const n = tracker.current.names(Date.now(), selfName); setTypers((cur) => (cur.join("|") === n.join("|") ? cur : n)); }, 1000);
    return () => { off(); window.clearInterval(tick); };
  }, [bus, selfName]);

  const merge = useCallback((incoming: ChatMessage[]) => {
    setItems((cur) => {
      const seen = new Set(cur.map((m) => m.id));
      const add = incoming.filter((m) => !seen.has(m.id));
      return add.length ? [...cur, ...add].sort((a, b) => a.id - b.id) : cur;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setItems([]); setLoaded(false); setHasMore(false); setError("");
    api.chat(meetingId).then((r) => { if (!cancelled) { setItems(r.messages); setHasMore(r.has_more); setLoaded(true); } })
      .catch((e) => { if (!cancelled) { setError((e as ApiError).message || "Не удалось загрузить чат"); setLoaded(true); } });
    return () => { cancelled = true; };
  }, [meetingId]);

  // новые сообщения по WebSocket; после переподключения — догрузка пропущенного (по id последнего)
  useEffect(() => {
    if (!bus) return;
    const offEv = bus.on((e) => {
      if (e.type !== "chat_message") return;
      merge([e.message]);
      tracker.current.event(e.message.author_id ?? "", e.message.author_name, false);          // сообщение пришло — автор больше не «печатает»
      if (e.message.author_name !== selfName) playChatSound();            // чужое сообщение — один мягкий сигнал (серия сливается), своё — без звука
      if (!visibleRef.current && e.message.author_name !== selfName) { unread.current += 1; cb.current.onUnread?.(unread.current); }
    });
    const offSync = bus.onResync(() => {
      const last = itemsRef.current.length ? itemsRef.current[itemsRef.current.length - 1].id : 0;
      api.chat(meetingId, { afterId: last, limit: 500 }).then((r) => merge(r.messages)).catch(() => undefined);
    });
    return () => { offEv(); offSync(); };
  }, [bus, meetingId, merge, selfName]);

  useEffect(() => { if (visible) { unread.current = 0; cb.current.onUnread?.(0); } }, [visible]);

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    if (keepRef.current) { // подгрузили более ранние сообщения — остаёмся на том же месте
      el.scrollTop = el.scrollHeight - keepRef.current.h + keepRef.current.t;
      keepRef.current = null;
    } else if (stickRef.current) el.scrollTop = el.scrollHeight;
  }, [items, visible]);

  const loadOlder = useCallback(async () => {
    const first = itemsRef.current[0];
    const el = boxRef.current;
    if (!first || !el) return;
    try {
      const r = await api.chat(meetingId, { beforeId: first.id });
      keepRef.current = { h: el.scrollHeight, t: el.scrollTop };
      setHasMore(r.has_more);
      merge(r.messages);
    } catch { /* повторится при следующей прокрутке */ }
  }, [meetingId, merge]);

  const onScroll = () => {
    const el = boxRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (el.scrollTop < 60 && hasMore) { setHasMore(false); void loadOlder(); }
  };

  useLayoutEffect(() => { autoGrow(inputRef.current, 40, 140); }, [draft]);

  useEffect(() => () => { for (const p of pendingRef.current) if (p.preview) URL.revokeObjectURL(p.preview); }, []);

  const addFiles = (files: File[]) => {
    if (!canAttach || readOnly || !files.length) return;
    setError("");
    for (const file of files) {
      const key = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : "";
      setPending((cur) => [...cur, { key, file, preview, state: "uploading" }]);
      api.uploadAttachment(meetingId, file)
        .then((att) => setPending((cur) => cur.map((p) => p.key === key ? { ...p, state: "ready", att } : p)))
        .catch((e) => setPending((cur) => cur.map((p) => p.key === key ? { ...p, state: "error", error: (e as ApiError).message || "Не удалось загрузить файл" } : p)));
    }
  };
  const removePending = (p: Pending) => {
    if (p.preview) URL.revokeObjectURL(p.preview);
    setPending((cur) => cur.filter((x) => x.key !== p.key));
    if (p.att) void api.discardAttachment(meetingId, p.att.id).catch(() => undefined);
  };
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? []);
    if (!files.length || !canAttach) return;
    e.preventDefault();   // скриншот из буфера — вложение, а не текст
    addFiles(files.map((f, i) => { const name = pastedName(f.name, f.type, new Date(), i, files.length); return name === f.name ? f : new File([f], name, { type: f.type }); }));
  };
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault(); setDragOver(false);
    addFiles(Array.from(e.dataTransfer.files ?? []));
  };
  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!canAttach || readOnly || !Array.from(e.dataTransfer.types ?? []).includes("Files")) return;
    e.preventDefault(); setDragOver(true);
  };

  const uploading = pending.some((p) => p.state === "uploading");
  const ready = pending.filter((p) => p.state === "ready" && p.att);
  const canSend = !sending && !uploading && (!!draft.trim() || ready.length > 0);

  const send = async () => {
    const text = draft.trim();
    if (!canSend) return;
    setSending(true); setError("");
    try {
      const m = await api.sendChat(meetingId, text, ready.map((p) => p.att!.id));
      merge([m]);
      stickRef.current = true;
      setDraft("");
      sender.current?.stop();
      for (const p of ready) if (p.preview) URL.revokeObjectURL(p.preview);
      setPending((cur) => cur.filter((p) => p.state === "error"));
    } catch (e) { setError((e as ApiError).message || "Не удалось отправить сообщение"); }
    setSending(false);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  };

  return (
    <div className={`chat ${dragOver ? "drop" : ""}`} hidden={!visible} onDragOver={onDragOver} onDragLeave={(e) => { if (e.currentTarget === e.target) setDragOver(false); }} onDrop={onDrop}>
      <div className="chat-list" ref={boxRef} onScroll={onScroll} aria-live="polite" aria-label="Сообщения чата">
        {hasMore && <button type="button" className="btn mini ghost chat-older" onClick={() => { setHasMore(false); void loadOlder(); }}>Показать более ранние</button>}
        {loaded && items.length === 0 && !error && <p className="muted small">{readOnly ? "В этой встрече чат не использовался." : "Пока пусто. Адреса, ссылки, имена серверов и номера задач попадут в протокол."}</p>}
        {items.map((m) => <Message key={m.id} m={m} mine={!!selfName && m.author_name === selfName} meetingId={meetingId} />)}
      </div>
      {error && <div className="alert error small" role="alert">{error}</div>}
      {!readOnly && (
        <div className="chat-compose">
          {pending.length > 0 && (
            <ul className="chat-pending" aria-label="Вложения к сообщению">
              {pending.map((p) => (
                <li key={p.key} className={`chat-pend ${p.state}`} title={p.error || p.file.name}>
                  {p.preview ? <img src={p.preview} alt="" /> : <Icon name="file" size={18} />}
                  <span className="chat-pend-info">
                    <span className="chat-file-name">{p.file.name}</span>
                    <span className="small muted">{p.state === "uploading" ? "Загрузка…" : p.state === "error" ? <span className="err">{p.error}</span> : formatSize(p.file.size)}</span>
                  </span>
                  <button type="button" className="icon-btn" onClick={() => removePending(p)} title="Убрать вложение" aria-label={`Убрать ${p.file.name}`}><Icon name="close" size={14} /></button>
                </li>
              ))}
            </ul>
          )}
          <div className="chat-typing" aria-live="polite" role="status">{typingText(typers)}</div>
          <div className="chat-box">
            {canAttach && (
              <>
                <input ref={fileRef} type="file" multiple hidden onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
                <button type="button" className="chat-attach" onClick={() => fileRef.current?.click()} title="Прикрепить файл" aria-label="Прикрепить файл"><Icon name="attach" size={18} /></button>
              </>
            )}
            <textarea ref={inputRef} value={draft} maxLength={MAX_LEN} rows={1} placeholder="Сообщение" aria-label="Текст сообщения"
                      onChange={(e) => { setDraft(e.target.value); if (e.target.value.trim()) sender.current?.input(); else sender.current?.stop(); }} onKeyDown={onKey} onPaste={onPaste} />
            <button type="button" className="chat-send" disabled={!canSend} onClick={() => void send()} title="Отправить (Enter)" aria-label="Отправить сообщение">
              <Icon name="send" size={18} />
            </button>
          </div>
          <div className="chat-hint">Enter — отправить, Shift+Enter — новая строка{canAttach ? " · файл можно перетащить или вставить (Ctrl+V)" : ""}</div>
        </div>
      )}
    </div>
  );
}
