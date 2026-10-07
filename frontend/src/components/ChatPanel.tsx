import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, type ApiError, type ChatMessage } from "../api";
import type { LiveBus } from "../liveSocket";
import { linkify } from "../linkify";
import { formatTime } from "../transcript";
import { copyText } from "../util";

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

function Message({ m, mine }: { m: ChatMessage; mine: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { if (await copyText(m.text)) { setCopied(true); window.setTimeout(() => setCopied(false), 1500); } };
  return (
    <div className={`chat-msg ${mine ? "mine" : ""} ${m.author_type === "guest" ? "guest" : ""}`}>
      <div className="chat-meta">
        <strong>{m.author_name}</strong>
        <time dateTime={m.created_at} title={new Date(m.created_at).toLocaleString("ru-RU")}>{formatTime(m.created_at)}</time>
        <button type="button" className="chat-copy" onClick={copy} title="Копировать текст сообщения" aria-label="Копировать сообщение">{copied ? "Скопировано" : "Копировать"}</button>
      </div>
      <MessageText text={m.text} />
    </div>
  );
}

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
}

/** Компактный рабочий чат встречи: сообщения, время и автор, ссылки, копирование, история. Не мессенджер: без реакций и вложений. */
export default function ChatPanel({ meetingId, bus, readOnly = false, visible = true, selfName, onUnread }: Props) {
  const [items, setItems] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const keepRef = useRef<{ h: number; t: number } | null>(null);
  const unread = useRef(0);
  const itemsRef = useRef<ChatMessage[]>([]);
  itemsRef.current = items;
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const cb = useRef({ onUnread });
  cb.current = { onUnread };

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

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true); setError("");
    try {
      const m = await api.sendChat(meetingId, text);
      merge([m]);
      stickRef.current = true;
      setDraft("");
    } catch (e) { setError((e as ApiError).message || "Не удалось отправить сообщение"); }
    setSending(false);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  };

  return (
    <div className="chat" hidden={!visible}>
      <div className="chat-list" ref={boxRef} onScroll={onScroll} aria-live="polite" aria-label="Сообщения чата">
        {hasMore && <button type="button" className="btn mini ghost chat-older" onClick={() => { setHasMore(false); void loadOlder(); }}>Показать более ранние</button>}
        {loaded && items.length === 0 && !error && <p className="muted small">{readOnly ? "В этой встрече чат не использовался." : "Сообщений пока нет. Сюда удобно писать адреса, ссылки, имена серверов и номера задач — они попадут в материалы встречи и протокол."}</p>}
        {items.map((m) => <Message key={m.id} m={m} mine={!!selfName && m.author_name === selfName} />)}
      </div>
      {error && <div className="alert error small" role="alert">{error}</div>}
      {!readOnly && (
        <div className="chat-compose">
          <textarea value={draft} maxLength={MAX_LEN} rows={2} placeholder="Сообщение (Enter — отправить, Shift+Enter — новая строка)" aria-label="Текст сообщения"
                    onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} />
          <button type="button" className="btn primary" disabled={sending || !draft.trim()} onClick={() => void send()}>Отправить</button>
        </div>
      )}
    </div>
  );
}
