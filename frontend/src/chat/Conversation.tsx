import { Fragment, Suspense, forwardRef, lazy, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useActiveMeeting } from "../activeMeeting";
import Avatar from "../components/Avatar";
import { useContextMenu, type MenuItem } from "../components/ContextMenu";
import { Icon } from "../components/Icons";
import { chatApi, newClientId, type ChatFileInfo, type ChatInfo, type ChatItem, type ChatMsg, type SearchResult } from "./chatApi";
import { ForwardDialog, ReceiptsDialog } from "./ChatDialogs";
import { fileSize, format, insertDictated, mentionQuery, mentionsMe, rows, timeHM, type Mentions, type Span } from "./chatModel";
import EmojiPicker from "./EmojiPicker";
import { ReactBar, ReactionChips } from "./Reactions";
import { insertAt } from "./emoji";
import { chats, useChats } from "./chatStore";
import { Dictation, type DictationPhase } from "./dictation";
import { chatMenu } from "./ChatWindow";
import { reportEvent } from "../diagnostics";
import { withRetry } from "../uploadVerify";

const ImageViewer = lazy(() => import("../components/ImageViewer"));

type Ask = (title: string, body: string, label: string, run: () => Promise<void>) => void;

export default function Conversation({ chat, onInfo, ask, jump, onJumped, searchSignal }: {
  chat: ChatItem; onInfo: () => void; ask: Ask; jump: { id: number; terms: string[] } | null; onJumped: () => void; searchSignal: number;
}) {
  const s = useChats();
  const conv = s.convs[chat.id];
  const me = s.config?.me.id ?? "";
  const [info, setInfo] = useState<ChatInfo | null>(null);
  const [search, setSearch] = useState(false);
  const [marks, setMarks] = useState<string[]>([]);
  const [reply, setReply] = useState<ChatMsg | null>(null);
  const [edit, setEdit] = useState<ChatMsg | null>(null);
  const [forward, setForward] = useState<ChatMsg | null>(null);
  const [receipts, setReceipts] = useState<ChatMsg | null>(null);
  const [viewer, setViewer] = useState<{ images: ChatFileInfo[]; index: number } | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  const [reactAt, setReactAt] = useState<{ m: ChatMsg; rect: DOMRect; full: boolean } | null>(null);
  const menu = useContextMenu();
  const list = useRef<ListHandle>(null);

  useEffect(() => { void chats.select(chat.id); }, [chat.id]);
  useEffect(() => { void chatApi.info(chat.id).then(setInfo).catch(() => setInfo(null)); }, [chat.id, conv?.version, s.listVersion]);
  useEffect(() => { if (searchSignal) setSearch(true); }, [searchSignal]);

  const goTo = useCallback(async (id: number, terms?: string[]) => {
    await chats.jumpTo(chat.id, id);
    if (terms) setMarks(terms);
    window.requestAnimationFrame(() => list.current?.scrollToMessage(id));
    setFlash(id);
    window.setTimeout(() => setFlash((f) => (f === id ? null : f)), 1800);
  }, [chat.id]);
  useEffect(() => { if (jump) { void goTo(jump.id, jump.terms); onJumped(); } }, [jump, goTo, onJumped]);

  const peer = info?.kind === "direct" ? info.members.find((m) => m.id !== me) : null;
  const typing = Object.values(conv?.typing ?? {}).filter((t) => t.until > Date.now()).map((t) => t.name);
  const status = typing.length ? `${typing.join(", ")} печата${typing.length > 1 ? "ют" : "ет"}…`
    : chat.kind === "group" ? `${chat.members} участник${plural(chat.members)}`
    : peer ? (s.config?.presence && peer.online ? "в сети" : [peer.title, peer.department].filter(Boolean).join(" · ") || (peer.active ? "" : "учётная запись отключена")) : "";

  const headMenu = (): MenuItem[] => chatMenu(chat, s.folders, {
    info: onInfo, search: () => setSearch(true), presence: s.config?.presence, watching: !!peer?.watch,
    watch: peer ? () => void chatApi.watch(peer.id, !peer.watch).then(() => chatApi.info(chat.id).then(setInfo)) : undefined,
  }, ask);

  const who: Mentions = useMemo(() => ({ names: (info?.members ?? []).map((x) => x.name), me: s.config?.me.name ?? "" }), [info, s.config?.me.name]);
  const react = (m: ChatMsg, emoji: string | null) => void chatApi.react(chat.id, m.id, emoji).then((x) => chats.applyMessage(chat.id, x)).catch((e) => alert((e as Error).message));
  const openReact = (m: ChatMsg, el: HTMLElement | null) => { const r = (el ?? document.querySelector(`[data-mid="${m.id}"] .msg-bubble`))?.getBoundingClientRect(); if (r) setReactAt({ m, rect: r, full: false }); };
  const lastOwn = () => [...(conv?.messages ?? [])].reverse().find((m) => m.author_id === me && m.kind === "text" && !m.deleted && !m.pending) ?? null;

  const msgMenu = (m: ChatMsg): MenuItem[] => {
    const mine = m.author_id === me;
    return [
      { id: "react", label: "Реакция", icon: "smile", onSelect: () => openReact(m, null), hidden: m.deleted || !!m.pending },
      { id: "reply", label: "Ответить", icon: "reply", onSelect: () => { setEdit(null); setReply(m); } },
      { id: "copy", label: "Скопировать текст", icon: "copy", onSelect: () => void navigator.clipboard?.writeText(m.text), hidden: !m.text },
      { id: "fwd", label: "Переслать", icon: "forward", onSelect: () => setForward(m), hidden: !m.text },
      { id: "edit", label: "Изменить", icon: "edit", onSelect: () => { setReply(null); setEdit(m); }, hidden: !mine || m.kind !== "text" },
      { id: "info", label: "Доставка и прочтение", icon: "checks", onSelect: () => setReceipts(m), hidden: !mine },
      { id: "del", label: "Удалить", icon: "trash", danger: true, hidden: !mine, confirm: "Удалить сообщение у всех участников? Вложения тоже удалятся.",
        onSelect: () => void chatApi.remove(chat.id, m.id).catch((e) => alert((e as Error).message)) },
    ];
  };

  return (
    <div className="conv">
      <header className="conv-head" data-drag>
        <button type="button" className="btn ghost mini icon-btn conv-back" aria-label="К списку чатов" onClick={() => void chats.select(null)}><Icon name="chevronL" size={20} /></button>
        <button type="button" className="conv-who" onClick={onInfo} title={chat.kind === "group" ? "Информация о группе" : "Информация о собеседнике"}>
          <span className="chat-av"><Avatar name={chat.title} url={chat.avatar_url} size={38} />{s.config?.presence && peer?.online && <span className="chat-online" aria-label="в сети" />}</span>
          <span className="conv-who-text"><b>{chat.title}</b><span className={`conv-status ${typing.length ? "typing" : ""}`}>{status}</span></span>
        </button>
        <div className="spacer" />
        <button type="button" className={`btn ghost mini icon-btn ${search ? "on" : ""}`} aria-label="Поиск по сообщениям" title="Поиск по сообщениям" aria-pressed={search}
                onClick={() => { setSearch((v) => !v); setMarks([]); }}><Icon name="search" size={18} /></button>
        <button type="button" className="btn ghost mini icon-btn" aria-label="Действия с чатом" title="Ещё"
                onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); menu.openAt(r.right - 240, r.bottom + 4, headMenu()); }}><Icon name="more" size={18} /></button>
      </header>
      {search && <SearchBar chat={chat} info={info} onGo={goTo} onMarks={setMarks} onClose={() => { setSearch(false); setMarks([]); }} />}
      <MessageList ref={list} chat={chat} me={me} who={who} marks={marks} flash={flash} onMenu={(m) => menu.onContextMenu(() => msgMenu(m))} onReplyJump={(id) => void goTo(id)}
                   onReact={openReact} onToggleReact={react}
                   onImage={(images, index) => setViewer({ images, index })} onReply={(m) => { setEdit(null); setReply(m); }}
                   onMore={(m, e) => { const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); menu.openAt(r.left, r.bottom + 4, msgMenu(m)); }} />
      <Composer chat={chat} reply={reply} edit={edit} onDone={() => { setReply(null); setEdit(null); window.requestAnimationFrame(() => list.current?.toBottom()); }}
                onCancel={() => { setReply(null); setEdit(null); }} members={chat.kind === "group" ? (info?.members ?? []).filter((x) => x.id !== me) : []}
                onEditLast={() => { const m = lastOwn(); if (m) { setReply(null); setEdit(m); } return !!m; }} />
      {reactAt && !reactAt.full && (
        <ReactBar at={reactAt.rect} choices={(s.config?.reactions ?? []).slice(0, 6)} current={reactAt.m.reactions?.find((r) => r.user_ids.includes(me))?.emoji ?? null}
                  onPick={(e) => { react(reactAt.m, e); setReactAt(null); }} onMore={() => setReactAt({ ...reactAt, full: true })} onClose={() => setReactAt(null)} />
      )}
      {reactAt?.full && <EmojiPicker anchor={reactAt.rect} only={s.config?.reactions ?? []} title="Реакция" onPick={(e) => { react(reactAt.m, e); setReactAt(null); }} onClose={() => setReactAt(null)} />}
      {menu.node}
      {forward && <ForwardDialog from={chat.id} msg={forward} onClose={() => setForward(null)} />}
      {receipts && <ReceiptsDialog chatId={chat.id} msg={receipts} onClose={() => setReceipts(null)} />}
      {viewer && (
        <Suspense fallback={null}>
          <ImageViewer images={viewer.images.map((f) => ({ id: f.id, name: f.name, size: f.size }))} index={viewer.index} load={(id) => chatApi.fileBlob(chat.id, id)}
                       onClose={() => setViewer(null)} onIndex={(i) => setViewer((v) => (v ? { ...v, index: i } : v))} />
        </Suspense>
      )}
    </div>
  );
}

const TOUCH = () => window.matchMedia?.("(hover: none)").matches ?? false;
const plural = (n: number) => (n % 10 === 1 && n % 100 !== 11 ? "" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "а" : "ов");

// ------------------------------------------------------------------------------------------------ лента
interface ListHandle { scrollToMessage: (id: number) => void; toBottom: () => void }
const MessageList = forwardRef<ListHandle, {
  chat: ChatItem; me: string; who: Mentions; marks: string[]; flash: number | null; onMenu: (m: ChatMsg) => (e: React.MouseEvent) => void; onMore: (m: ChatMsg, e: React.MouseEvent) => void;
  onReplyJump: (id: number) => void; onImage: (images: ChatFileInfo[], index: number) => void; onReply: (m: ChatMsg) => void;
  onReact: (m: ChatMsg, el: HTMLElement | null) => void; onToggleReact: (m: ChatMsg, emoji: string | null) => void;
}>(function MessageList({ chat, me, who, marks, flash, onMenu, onMore, onReplyJump, onImage, onReply, onReact, onToggleReact }, ref) {
  const s = useChats();
  const conv = s.convs[chat.id];
  const box = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const anchor = useRef<{ h: number; top: number } | null>(null);
  const [newBelow, setNewBelow] = useState(0);
  const firstUnread = useRef<number | null>(null);
  const msgs = conv?.messages ?? [];
  const list = useMemo(() => rows(msgs), [msgs]);
  const reads = conv?.reads ?? {};
  const readTop = Math.max(0, ...Object.values(reads));

  useImperativeHandle(ref, () => ({
    scrollToMessage: (id) => { box.current?.querySelector(`[data-mid="${id}"]`)?.scrollIntoView({ block: "center" }); },
    toBottom: () => { const b = box.current; if (b) { b.scrollTop = b.scrollHeight; atBottom.current = true; setNewBelow(0); } },
  }));

  // при открытии: к первому непрочитанному, иначе вниз
  useEffect(() => { firstUnread.current = chat.unread ? chat.last_read_id : null; }, [chat.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const opened = useRef(false);
  useLayoutEffect(() => {
    const b = box.current;
    if (!b || !conv?.loaded) return;
    if (anchor.current) {                                       // догрузили старые сверху — позиция чтения не прыгает
      b.scrollTop = b.scrollHeight - anchor.current.h + anchor.current.top;
      anchor.current = null;
      return;
    }
    if (!opened.current) {
      opened.current = true;
      const mark = firstUnread.current !== null ? b.querySelector(".msg-unread-mark") : null;
      if (mark) (mark as HTMLElement).scrollIntoView({ block: "start" }); else b.scrollTop = b.scrollHeight;
      return;
    }
    const last = msgs[msgs.length - 1];
    if (atBottom.current || (last && last.author_id === me && last.pending)) { b.scrollTop = b.scrollHeight; setNewBelow(0); }
    else if (last && last.author_id !== me) setNewBelow((n) => n + 1);
  }, [msgs, conv?.loaded]);  // eslint-disable-line react-hooks/exhaustive-deps

  const onScroll = () => {
    const b = box.current;
    if (!b) return;
    atBottom.current = b.scrollHeight - b.scrollTop - b.clientHeight < 80;
    if (atBottom.current) { setNewBelow(0); chats.scheduleRead(chat.id); }
    if (b.scrollTop < 120 && conv?.hasMoreBefore && !conv.loading) {
      anchor.current = { h: b.scrollHeight, top: b.scrollTop };
      void chats.loadOlder(chat.id).then((n) => { if (!n) anchor.current = null; });
    }
  };

  const images = msgs.flatMap((m) => m.files.filter((f) => f.kind === "image" && !f.missing));
  const unreadAt = firstUnread.current;
  let markShown = false;
  return (
    <div className="msgs-wrap">
      <div className="msgs" ref={box} onScroll={onScroll} role="log" aria-live="polite" aria-label="Сообщения">
        {conv?.loading && <div className="msgs-loading muted small">Загрузка…</div>}
        {conv?.error && <div className="alert error">{conv.error} <button type="button" className="btn mini" onClick={() => void chats.loadLatest(chat.id)}>Повторить</button></div>}
        {conv?.loaded && !conv.hasMoreBefore && msgs.length > 0 && <div className="msgs-start muted small">Начало переписки</div>}
        {conv?.loaded && msgs.length === 0 && <div className="msgs-empty muted">Сообщений пока нет — напишите первым.</div>}
        {list.map((r) => {
          if (r.kind === "day") return <div key={r.key} className="msg-day"><span>{r.title}</span></div>;
          const m = r.m;
          const showMark = !markShown && unreadAt !== null && !m.pending && m.id > unreadAt && m.author_id !== me;
          if (showMark) markShown = true;
          return (
            <Fragment key={r.key}>
              {showMark && <div className="msg-unread-mark"><span>Новые сообщения</span></div>}
              {m.kind === "system"
                ? <div className="msg-system" data-mid={m.id}>{m.text}</div>
                : <Bubble m={m} mine={m.author_id === me} me={me} who={who} group={chat.kind === "group"} first={r.first} last={r.last} read={!m.pending && readTop >= m.id} marks={marks}
                          onReact={(el) => onReact(m, el)} onToggleReact={(e) => onToggleReact(m, e)}
                          flash={flash === m.id} chatId={chat.id} onMenu={onMenu(m)} onMore={(e) => onMore(m, e)} onReplyJump={onReplyJump} onReply={() => onReply(m)}
                          onImage={(f) => onImage(images, Math.max(0, images.findIndex((x) => x.id === f.id)))} />}
            </Fragment>
          );
        })}
      </div>
      {newBelow > 0 && (
        <button type="button" className="msgs-new" onClick={() => { const b = box.current; if (b) b.scrollTop = b.scrollHeight; setNewBelow(0); }}>
          <Icon name="chevronD" size={16} /> Новые: {newBelow}
        </button>
      )}
    </div>
  );
});

function Bubble({ m, mine, me, who, group, first, last, read, marks, flash, chatId, onMenu, onMore, onReplyJump, onReply, onImage, onReact, onToggleReact }: {
  m: ChatMsg; mine: boolean; me: string; who: Mentions; group: boolean; first: boolean; last: boolean; read: boolean; marks: string[]; flash: boolean; chatId: string;
  onMenu: (e: React.MouseEvent) => void; onMore: (e: React.MouseEvent) => void; onReplyJump: (id: number) => void; onReply: () => void; onImage: (f: ChatFileInfo) => void;
  onReact: (el: HTMLElement | null) => void; onToggleReact: (emoji: string | null) => void;
}) {
  const bubble = useRef<HTMLDivElement>(null);
  const mentioned = !mine && mentionsMe(m.text, who.me);
  return (
    <div className={`msg ${mine ? "mine" : "theirs"} ${first ? "first" : ""} ${last ? "last" : ""} ${flash ? "flash" : ""} ${mentioned ? "mentioned" : ""} ${m.pending ? `pending ${m.pending}` : ""}`}
         data-mid={m.pending ? undefined : m.id} onContextMenu={onMenu}>
      {!mine && group && <span className="msg-av">{last ? <Avatar name={m.author_name} size={28} /> : null}</span>}
      <div className="msg-bubble" ref={bubble} onClick={(e) => { if (TOUCH() && !m.pending && !(e.target as HTMLElement).closest("a, button, .msg-thumb, .msg-file")) onMore(e); }}>
        {!mine && group && first && <div className="msg-author">{m.author_name}</div>}
        {m.forwarded && <div className="msg-fwd"><Icon name="forward" size={13} /> Переслано от {m.forwarded.name}</div>}
        {m.reply_to && (
          <button type="button" className="msg-quote" onClick={() => onReplyJump(m.reply_to!.id)}>
            <b>{m.reply_to.author_name}</b><span>{m.reply_to.deleted ? "Сообщение удалено" : m.reply_to.text || "Вложение"}</span>
          </button>
        )}
        {m.deleted ? <div className="msg-text deleted">Сообщение удалено</div> : m.text && <div className="msg-text"><Spans spans={format(m.text, marks, who)} /></div>}
        {!m.deleted && m.files.length > 0 && (
          <div className="msg-files">
            {m.files.map((f) => f.kind === "image" && !f.missing
              ? <Thumb key={f.id} chatId={chatId} f={f} onOpen={() => onImage(f)} />
              : (
                <a key={f.id} className={`msg-file ${f.missing ? "missing" : ""}`} href={f.missing ? undefined : chatApi.fileUrl(chatId, f.id, true)} download={f.name}>
                  <Icon name="file" size={22} />
                  <span><b>{f.name}</b><span className="muted small">{f.missing ? "файл недоступен" : `${fileSize(f.size)} · ${(f.name.split(".").pop() ?? "").toUpperCase()}`}</span></span>
                  {!f.missing && <Icon name="download" size={16} />}
                </a>
              ))}
          </div>
        )}
        {!m.deleted && <ReactionChips items={m.reactions} me={me} onToggle={onToggleReact} />}
        <div className="msg-meta">
          {m.edited_at && !m.deleted && <span className="msg-edited" title={`Изменено ${new Date(m.edited_at).toLocaleString("ru-RU")}`} aria-label="изменено"><Icon name="edit" size={12} /></span>}
          <time dateTime={m.created_at} title={new Date(m.created_at).toLocaleString("ru-RU")}>{timeHM(m.created_at)}</time>
          {mine && (m.pending === "sending" ? <Icon name="clock" size={13} aria-label="отправляется" />
            : m.pending === "failed" ? <span className="msg-failed">не отправлено <button type="button" className="btn mini" onClick={() => void chats.retry(chatId, m).catch(() => undefined)}>Повторить</button>
                <button type="button" className="btn mini ghost" onClick={() => chats.discard(chatId, m)}>Убрать</button></span>
            : <Icon name={read ? "checks" : "check"} size={14} className={read ? "msg-read" : ""} aria-label={read ? "прочитано" : "доставлено"} />)}
        </div>
      </div>
      {!m.pending && !m.deleted && (
        <div className="msg-actions">
          <button type="button" className="btn ghost mini icon-btn" aria-label="Реакция" title="Реакция" onClick={() => onReact(bubble.current)}><Icon name="smile" size={15} /></button>
          <button type="button" className="btn ghost mini icon-btn" aria-label="Ответить" title="Ответить" onClick={onReply}><Icon name="reply" size={15} /></button>
          <button type="button" className="btn ghost mini icon-btn" aria-label="Действия с сообщением" title="Ещё" onClick={onMore}><Icon name="more" size={15} /></button>
        </div>
      )}
    </div>
  );
}

function Spans({ spans }: { spans: Span[] }): ReactNode {
  return spans.map((s, i) => {
    switch (s.t) {
      case "text": return <Fragment key={i}>{s.v}</Fragment>;
      case "br": return <br key={i} />;
      case "link": return <a key={i} href={s.href} target="_blank" rel="noopener noreferrer nofollow">{s.v}</a>;
      case "b": return <strong key={i}><Spans spans={s.c} /></strong>;
      case "i": return <em key={i}><Spans spans={s.c} /></em>;
      case "code": return <code key={i}>{s.v}</code>;
      case "pre": return <pre key={i}>{s.v}</pre>;
      case "mark": return <mark key={i}>{s.v}</mark>;
      case "mention": return <span key={i} className={`mention ${s.me ? "me" : ""}`}>{s.v}</span>;
    }
    return null;
  });
}

const thumbs = new Map<string, string>();
function Thumb({ chatId, f, onOpen }: { chatId: string; f: ChatFileInfo; onOpen: () => void }) {
  const [url, setUrl] = useState(thumbs.get(f.id) ?? "");
  const [err, setErr] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (url) return;
    let alive = true;
    setErr("");
    withRetry(() => chatApi.fileBlob(chatId, f.id)).then((b) => { const u = URL.createObjectURL(b); thumbs.set(f.id, u); if (alive) setUrl(u); })
      .catch((e) => { if (!alive) return; const x = e as { status?: number; code?: string; message?: string }; setErr(x.status ? `HTTP ${x.status}` : "нет связи");
        reportEvent("chat_attachment_failed", { reason: x.status ? `http_${x.status}` : x.code || "error", detail: `корпоративный чат, ${f.size ?? "?"} байт: ${x.message ?? ""}`.slice(0, 200) }); });
    return () => { alive = false; };
  }, [chatId, f.id, url, attempt]);
  return (
    <button type="button" className="msg-thumb" onClick={err ? () => setAttempt((n) => n + 1) : onOpen} aria-label={err ? `Повторить загрузку: ${f.name}` : `Открыть изображение ${f.name}`}>
      {url ? <img src={url} alt={f.name} loading="lazy" onError={() => { thumbs.delete(f.id); setUrl(""); setErr("файл повреждён"); }} />
        : <span className="muted small">{err ? `не загрузилось (${err}) — нажмите, чтобы повторить` : "…"}</span>}
    </button>
  );
}

// ------------------------------------------------------------------------------------------------ поиск в переписке
function SearchBar({ chat, info, onGo, onMarks, onClose }: { chat: ChatItem; info: ChatInfo | null; onGo: (id: number, terms: string[]) => void; onMarks: (t: string[]) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [author, setAuthor] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [res, setRes] = useState<SearchResult | null>(null);
  const [i, setI] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const run = useCallback(async () => {
    if (q.trim().length < 1) { setRes(null); onMarks([]); return; }
    setBusy(true); setErr("");
    try {
      const r = await chatApi.search(chat.id, { q, author: author || undefined, date_from: from || undefined, date_to: to || undefined });
      setRes(r); setI(0); onMarks(r.terms);
      if (r.items[0]) onGo(r.items[0].id, r.terms);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }, [q, author, from, to, chat.id, onGo, onMarks]);
  useEffect(() => { const h = window.setTimeout(() => void run(), 350); return () => window.clearTimeout(h); }, [run]);
  const go = (n: number) => { if (!res?.items.length) return; const k = (n + res.items.length) % res.items.length; setI(k); onGo(res.items[k].id, res.terms); };
  return (
    <div className="conv-search" role="search">
      <div className="conv-search-row">
        <label className="chat-search grow"><Icon name="search" size={15} />
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Слово или «фраза в кавычках»" aria-label="Поиск по сообщениям этого чата"
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); go(e.shiftKey ? i - 1 : i + 1); } if (e.key === "Escape") { e.stopPropagation(); onClose(); } }} />
        </label>
        <span className="conv-search-count muted small" aria-live="polite">{busy ? "…" : res ? (res.total ? `${i + 1} из ${res.total}${res.more ? "+" : ""}` : "не найдено") : ""}</span>
        <button type="button" className="btn ghost mini icon-btn" aria-label="Предыдущее (ранее)" title="Раньше" disabled={!res?.total} onClick={() => go(i + 1)}><Icon name="chevronL" size={16} style={{ transform: "rotate(90deg)" }} /></button>
        <button type="button" className="btn ghost mini icon-btn" aria-label="Следующее (позже)" title="Позже" disabled={!res?.total} onClick={() => go(i - 1)}><Icon name="chevronD" size={16} /></button>
        <button type="button" className="btn ghost mini icon-btn" aria-label="Закрыть поиск" onClick={onClose}><Icon name="close" size={16} /></button>
      </div>
      <div className="conv-search-row filters">
        <label>Автор <select value={author} onChange={(e) => setAuthor(e.target.value)}><option value="">все</option>{info?.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
        <label>с <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>по <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      {err && <div className="alert error small">{err}</div>}
      {res && res.items.length > 0 && (
        <ul className="conv-search-hits">
          {res.items.slice(0, 50).map((h, k) => (
            <li key={h.id}><button type="button" className={k === i ? "on" : ""} onClick={() => { setI(k); onGo(h.id, res.terms); }}>
              <span className="muted small">{h.author_name} · {new Date(h.created_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
              <span><Spans spans={format(h.text, res.terms)} /></span>
            </button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ ввод
const DRAFT = (id: string) => `pg:chat:draft:${id}`;
const readDraft = (id: string) => { try { return localStorage.getItem(DRAFT(id)) ?? ""; } catch { return ""; } };
const writeDraft = (id: string, v: string) => { try { if (v.trim()) localStorage.setItem(DRAFT(id), v); else localStorage.removeItem(DRAFT(id)); } catch { /* не запоминается */ } };

function Composer({ chat, reply, edit, onDone, onCancel, members, onEditLast }: {
  chat: ChatItem; reply: ChatMsg | null; edit: ChatMsg | null; onDone: () => void; onCancel: () => void;
  members: { id: string; name: string; avatar_url: string | null }[]; onEditLast: () => boolean;
}) {
  const s = useChats();
  const cfg = s.config;
  const inMeeting = useActiveMeeting() !== null;
  const [text, setText] = useState(() => readDraft(chat.id));          // черновик переписки — в этом браузере, до отправки
  const [files, setFiles] = useState<(ChatFileInfo | { id: string; name: string; uploading: true; error?: string })[]>([]);
  const [err, setErr] = useState("");
  const [drag, setDrag] = useState(false);
  const [phase, setPhase] = useState<DictationPhase>("idle");
  const [dNote, setDNote] = useState("");
  const [level, setLevel] = useState(0);
  const [emoji, setEmoji] = useState<DOMRect | null>(null);
  const [mq, setMq] = useState<{ start: number; q: string } | null>(null);
  const [mi, setMi] = useState(0);
  const ta = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const lastTyping = useRef(0);
  const seen = useRef(new Set<number>());
  const dict = useRef<Dictation | null>(null);
  const textRef = useRef(text);
  textRef.current = text;

  useEffect(() => { if (edit) { setText(edit.text); window.requestAnimationFrame(() => ta.current?.focus()); } }, [edit]);
  useEffect(() => { if (reply) ta.current?.focus(); }, [reply]);
  useEffect(() => { const t = ta.current; if (t) { t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, 200)}px`; t.style.overflowY = t.scrollHeight > 200 ? "auto" : "hidden"; } }, [text]);
  useEffect(() => { if (edit) return; const h = window.setTimeout(() => writeDraft(chat.id, text), 300); return () => window.clearTimeout(h); }, [text, chat.id, edit]);
  useEffect(() => () => dict.current?.cancel(), []);                    // закрыли чат или переписку — микрофон освобождается
  useEffect(() => {
    const onDev = () => {
      const d = dict.current;
      if (d?.phase === "listening") void d.micLost().then((lost) => { if (lost && dict.current === d && d.phase === "listening") { void d.stop(); setDNote("Микрофон отключён — диктовка остановлена"); } });
    };
    navigator.mediaDevices?.addEventListener?.("devicechange", onDev);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", onDev);
  }, []);

  const upload = async (list: File[]) => {
    if (!cfg?.files.enabled) { setErr("Вложения отключены администратором"); return; }
    for (const f of list.slice(0, cfg.files.max_files)) {
      if (f.size > cfg.files.max_size_mb * 1024 * 1024) { setErr(`«${f.name}» больше ${cfg.files.max_size_mb} МБ`); continue; }
      const tmp = { id: `tmp-${newClientId()}`, name: f.name || "Снимок экрана.png", uploading: true as const };
      setFiles((x) => [...x, tmp]);
      try {
        const named = f.name ? f : new File([f], tmp.name, { type: f.type });
        const up = await chatApi.upload(chat.id, named);
        setFiles((x) => x.map((y) => (y.id === tmp.id ? up : y)));
      } catch (e) {
        setFiles((x) => x.filter((y) => y.id !== tmp.id));
        setErr((e as Error).message || "Не удалось загрузить файл");
      }
    }
  };

  const ready = files.filter((f): f is ChatFileInfo => !("uploading" in f));
  const uploading = files.some((f) => "uploading" in f);
  const canSend = !uploading && (text.trim().length > 0 || ready.length > 0) && phase !== "starting" && phase !== "finishing";

  const send = async (body = textRef.current) => {
    if (uploading) return;
    const t = body.trim();
    if (!t && !ready.length) return;
    setErr("");
    if (edit) {
      try { chats.applyMessage(chat.id, await chatApi.edit(chat.id, edit.id, t)); setText(readDraft(chat.id)); onDone(); } catch (e) { setErr((e as Error).message); }
      return;
    }
    const sent = ready;
    setText(""); setFiles([]); seen.current = new Set(); writeDraft(chat.id, "");
    onDone();
    try { await chats.send(chat.id, t, { replyTo: reply, files: sent }); } catch (e) { setErr((e as Error).message || "Не отправлено"); }
  };

  // ---- упоминания (@ в группе): подсказка участников по началу имени
  const found = mq ? members.filter((x) => x.name.toLocaleLowerCase("ru").split(/\s+/).some((w) => w.startsWith(mq.q.toLocaleLowerCase("ru"))) || !mq.q).slice(0, 8) : [];
  const putMention = (name: string) => {
    const el = ta.current;
    if (!mq || !el) return;
    const r = insertAt(text, mq.start, el.selectionStart, `@${name} `);
    setText(r.value); setMq(null);
    window.requestAnimationFrame(() => { el.focus(); el.selectionStart = el.selectionEnd = r.caret; });
  };
  const track = (el: HTMLTextAreaElement) => { setMq(members.length ? mentionQuery(el.value, el.selectionStart) : null); setMi(0); };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (found.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); setMi((i) => (i + (e.key === "ArrowDown" ? 1 : found.length - 1)) % found.length); return; }
    if (found.length && (e.key === "Enter" || e.key === "Tab")) { e.preventDefault(); putMention(found[mi].name); return; }
    if (found.length && e.key === "Escape") { e.stopPropagation(); setMq(null); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (canSend) void send(); }
    else if (e.key === "Escape" && (reply || edit)) { e.stopPropagation(); setText(edit ? readDraft(chat.id) : text); onCancel(); }
    else if (e.key === "ArrowUp" && !text && !edit && onEditLast()) e.preventDefault();      // ↑ в пустом поле — изменить своё последнее сообщение
  };
  const onChange = (el: HTMLTextAreaElement) => {
    setText(el.value);
    track(el);
    if (Date.now() - lastTyping.current > 3000 && el.value) { lastTyping.current = Date.now(); void chatApi.typing(chat.id).catch(() => undefined); }
  };
  const putEmoji = (e: string) => {
    const el = ta.current;
    const r = insertAt(textRef.current, el?.selectionStart ?? textRef.current.length, el?.selectionEnd ?? textRef.current.length, e);
    setText(r.value);
    window.requestAnimationFrame(() => { if (el) { el.focus(); el.selectionStart = el.selectionEnd = r.caret; } });
  };

  // ---- диктовка
  const startDictation = async () => {
    setDNote(""); seen.current = new Set();
    const d = new Dictation({
      onFinal: (seq, t) => {
        const el = ta.current;
        const caret = el && document.activeElement === el ? el.selectionStart : textRef.current.length;
        const r = insertDictated(textRef.current, caret, t, seq, seen.current);
        setText(r.value);
        window.requestAnimationFrame(() => { if (el) { el.selectionStart = el.selectionEnd = r.caret; } });
      },
      onPhase: (p, msg) => { setPhase(p); if (msg) setDNote(msg); if (p !== "listening") setLevel(0); },
      onLevel: (v) => setLevel(v),
    });
    dict.current = d;
    await d.start();
  };
  const stopDictation = async (andSend: boolean) => {
    const d = dict.current;
    if (!d) return;
    const ok = await d.stop();
    dict.current = null;
    if (andSend) {
      if (ok) await send();                                   // только после окончательного ответа: случайный обрыв ничего не отправит
      else setDNote("Окончательный текст не получен — проверьте и отправьте вручную");
    }
  };
  const cancelDictation = () => { dict.current?.cancel(); dict.current = null; };
  const listening = phase === "listening" || phase === "starting";
  const dictAvailable = !!cfg?.dictation.enabled && !!navigator.mediaDevices?.getUserMedia;

  return (
    <div className={`composer ${drag ? "drag" : ""}`}
         onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
         onDragLeave={(e) => { if (e.currentTarget === e.target) setDrag(false); }}
         onDrop={(e) => { e.preventDefault(); setDrag(false); void upload([...e.dataTransfer.files]); }}>
      {(reply || edit) && (
        <div className="composer-ctx">
          <Icon name={edit ? "edit" : "reply"} size={16} />
          <span><b>{edit ? "Изменение" : reply!.author_name}</b><span className="muted">{(edit ?? reply)!.text.slice(0, 120) || "Вложение"}</span></span>
          <button type="button" className="cbtn" aria-label="Отменить" title="Отменить (Esc)" onClick={() => { if (edit) setText(readDraft(chat.id)); onCancel(); }}><Icon name="close" size={14} /></button>
        </div>
      )}
      {files.length > 0 && (
        <div className="composer-files">
          {files.map((f) => (
            <span key={f.id} className="composer-file">
              <Icon name="file" size={14} />{f.name}{"uploading" in f ? <span className="muted small"> · …</span> : <span className="muted small"> · {fileSize(f.size)}</span>}
              {!("uploading" in f) && <button type="button" className="cbtn" style={{ width: 22, height: 22, minWidth: 22 }} aria-label={`Убрать ${f.name}`}
                onClick={() => { setFiles((x) => x.filter((y) => y.id !== f.id)); void chatApi.dropFile(chat.id, f.id).catch(() => undefined); }}><Icon name="close" size={12} /></button>}
            </span>
          ))}
        </div>
      )}
      {listening || phase === "finishing" ? (
        <div className="dictation" role="status" aria-live="polite">
          <span className="dictation-dot" style={{ transform: `scale(${1 + Math.min(level * 6, 0.8)})` }} aria-hidden />
          <b>{phase === "starting" ? "Микрофон…" : phase === "finishing" ? "Распознаём…" : "Идёт диктовка"}</b>
          {inMeeting && <span className="muted small" title="Если микрофон встречи включён, участники слышат диктовку">· вы во встрече</span>}
          <div className="spacer" />
          <button type="button" className="cbtn" disabled={phase !== "listening"} aria-label="Стоп" title="Остановить (текст останется в поле)" onClick={() => void stopDictation(false)}><Icon name="stop" size={16} /></button>
          <button type="button" className="cbtn send" disabled={phase !== "listening"} aria-label="Остановить и отправить" title="Остановить и отправить" onClick={() => void stopDictation(true)}><Icon name="send" size={16} /></button>
          <button type="button" className="cbtn" aria-label="Отмена" title="Отменить диктовку" onClick={cancelDictation}><Icon name="close" size={16} /></button>
        </div>
      ) : dNote ? <div className={`alert ${phase === "error" ? "error" : "warn"} small composer-note`}>{dNote} <button type="button" className="btn ghost mini" onClick={() => setDNote("")}>Понятно</button></div> : null}
      {err && <div className="alert error small composer-note">{err} <button type="button" className="btn ghost mini" onClick={() => setErr("")}>Скрыть</button></div>}
      {found.length > 0 && (
        <div className="mention-pop" role="listbox" aria-label="Упомянуть участника">
          {found.map((x, i) => (
            <button key={x.id} type="button" role="option" aria-selected={i === mi} className={i === mi ? "on" : ""} onMouseDown={(e) => { e.preventDefault(); putMention(x.name); }}>
              <Avatar name={x.name} url={x.avatar_url} size={22} />{x.name}
            </button>
          ))}
        </div>
      )}
      <div className="composer-row">
        <button type="button" className="cbtn" aria-label="Прикрепить файл" title={cfg?.files.enabled ? `Прикрепить файл (до ${cfg.files.max_size_mb} МБ) — или перетащите, или Ctrl+V` : "Вложения отключены"}
                disabled={!cfg?.files.enabled} onClick={() => picker.current?.click()}><Icon name="attach" size={20} /></button>
        <input ref={picker} type="file" multiple hidden onChange={(e) => { void upload([...(e.target.files ?? [])]); e.target.value = ""; }} />
        <textarea ref={ta} rows={1} value={text} maxLength={cfg?.max_message_chars ?? 4000} placeholder={edit ? "Изменение сообщения…" : "Сообщение…"}
                  title="Enter — отправить, Shift+Enter — новая строка, ↑ — изменить последнее, @ — упомянуть"
                  aria-label="Текст сообщения" onChange={(e) => onChange(e.target)} onKeyDown={onKey} onClick={(e) => track(e.currentTarget)} onBlur={() => window.setTimeout(() => setMq(null), 150)}
                  onPaste={(e) => { const imgs = [...e.clipboardData.files].filter((f) => f.type.startsWith("image/")); if (imgs.length) { e.preventDefault(); void upload(imgs); } }} />
        <button type="button" data-emoji-toggle className={`cbtn ${emoji ? "on" : ""}`} aria-label="Эмодзи" title="Эмодзи" onClick={(e) => setEmoji(emoji ? null : e.currentTarget.getBoundingClientRect())}><Icon name="smile" size={20} /></button>
        {dictAvailable && !listening && phase !== "finishing" && (
          <button type="button" className="cbtn" aria-label="Диктовка" title={cfg?.dictation.available ? "Продиктовать текст" : "Распознавание речи сейчас недоступно"}
                  disabled={!cfg?.dictation.available} onClick={() => void startDictation()}><Icon name="mic" size={20} /></button>
        )}
        <button type="button" className="cbtn send" aria-label={edit ? "Сохранить" : "Отправить"} title={edit ? "Сохранить (Enter)" : "Отправить (Enter)"} disabled={!canSend} onClick={() => void send()}>
          <Icon name={edit ? "check" : "send"} size={18} />
        </button>
      </div>
      {emoji && <EmojiPicker anchor={emoji} onPick={putEmoji} onClose={() => setEmoji(null)} />}
      {drag && <div className="composer-drop">Отпустите, чтобы прикрепить файлы</div>}
    </div>
  );
}
