import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Avatar from "../components/Avatar";
import FloatClose from "../components/FloatClose";
import { useContextMenu, type MenuItem } from "../components/ContextMenu";
import { ConfirmDialog } from "../components/Dialogs";
import { Icon } from "../components/Icons";
import { chatApi, type ChatItem, type Person, type SearchHit } from "./chatApi";
import { ChatInfoDialog, FoldersDialog, NewChatDialog, PrefsDialog } from "./ChatDialogs";
import { listTime, plainText, visibleChats, type ListView } from "./chatModel";
import { chats, useChats } from "./chatStore";
import type { Rect } from "../player/playerMath";

const LS_RECT = "pg:chat:rect";
const MIN = { w: 360, h: 380 };
const vp = () => ({ w: window.innerWidth, h: window.innerHeight });
/** Окно чатов целиком в пределах экрана: поле ввода внизу не должно уходить за край (в отличие от плеера, которому достаточно видимого заголовка). */
function clampRect(r: Rect, vw: number, vh: number): Rect {
  const w = Math.min(Math.max(r.w, Math.min(MIN.w, vw)), vw), h = Math.min(Math.max(r.h, Math.min(MIN.h, vh)), vh);
  return { x: Math.min(Math.max(r.x, 0), vw - w), y: Math.min(Math.max(r.y, 0), vh - h), w, h };
}
const MOBILE = 760;                 // уже — окно во весь экран, без перетаскивания
const ONE_PANE = 640;               // окно уже — одна панель (список или переписка), как на телефоне

/** Положение окна: запомненное (в пределах экрана) или справа под верхним меню. Хранится только в этом браузере. */
function initialRect(): Rect {
  const { w, h } = vp();
  try {
    const o = JSON.parse(localStorage.getItem(LS_RECT) || "null");
    if (o && ["x", "y", "w", "h"].every((k) => typeof o[k] === "number")) return clampRect({ x: o.x, y: o.y, w: Math.max(MIN.w, o.w), h: Math.max(MIN.h, o.h) }, w, h);
  } catch { /* нет сохранённого */ }
  const ww = Math.min(1000, w - 32), hh = Math.min(720, h - 72);
  return clampRect({ x: w - ww - 16, y: 56, w: ww, h: hh }, w, h);
}
const saveRect = (r: Rect) => { try { localStorage.setItem(LS_RECT, JSON.stringify({ x: r.x, y: r.y, w: r.w, h: r.h })); } catch { /* не запоминается */ } };
import Conversation from "./Conversation";

type Dialog = { kind: "new" } | { kind: "folders" } | { kind: "prefs" } | { kind: "info"; chatId: string } | { kind: "confirm"; title: string; body: string; label: string; run: () => Promise<void> } | null;

/** Меню переписки (правая кнопка по строке списка и «⋮» в заголовке переписки): одинаковые пункты, разрушительные — с подтверждением. */
export function chatMenu(c: ChatItem, folders: { id: string; name: string }[], h: { open?: () => void; info?: () => void; search?: () => void; watch?: () => void; watching?: boolean; presence?: boolean },
                         ask: (title: string, body: string, label: string, run: () => Promise<void>) => void): MenuItem[] {
  const set = (p: Parameters<typeof chatApi.setMine>[1]) => void chatApi.setMine(c.id, p).then(() => chats.refreshList());
  return [
    { id: "open", label: "Открыть чат", icon: "chat", onSelect: () => h.open?.(), hidden: !h.open },
    { id: "info", label: c.kind === "group" ? "Информация о группе" : "Информация о собеседнике", icon: "info", onSelect: () => h.info?.(), hidden: !h.info },
    { id: "search", label: "Поиск по сообщениям", icon: "search", onSelect: () => h.search?.(), hidden: !h.search },
    { id: "pin", label: c.pinned ? "Открепить" : "Закрепить", icon: c.pinned ? "unpin" : "pin", onSelect: () => set({ pinned: !c.pinned }) },
    { id: "folder", label: "Переместить в папку", icon: "folder", onSelect: () => undefined,
      submenu: [{ id: "none", label: "Без папки", checked: !c.folder_id, onSelect: () => set({ folder_id: null }) },
        ...folders.map((f) => ({ id: f.id, label: f.name, checked: c.folder_id === f.id, onSelect: () => set({ folder_id: f.id }) }))] },
    { id: "mute", label: c.muted ? "Включить уведомления" : "Отключить уведомления", icon: c.muted ? "bell" : "bellOff", onSelect: () => set({ muted: !c.muted }) },
    { id: "watch", label: h.watching ? "Не сообщать о появлении в сети" : "Сообщить, когда появится в сети", icon: "eye", onSelect: () => h.watch?.(), hidden: !h.watch || !h.presence || c.kind !== "direct" },
    { id: "arch", label: c.archived ? "Вернуть из архива" : "Архивировать", icon: "archive", onSelect: () => set({ archived: !c.archived }) },
    { id: "clear", label: "Очистить историю", icon: "trash", danger: true,
      onSelect: () => ask("Очистить историю?", "Сообщения исчезнут только у вас. У собеседников переписка останется без изменений.", "Очистить",
        async () => { await chatApi.clear(c.id); await chats.loadLatest(c.id); await chats.refreshList(); }) },
    { id: "hide", label: "Удалить чат из списка", icon: "close", danger: true,
      onSelect: () => ask("Удалить чат из списка?", "Переписка исчезнет из вашего списка и будет очищена у вас. У остальных участников ничего не удалится; новое сообщение вернёт чат в список.", "Удалить",
        async () => { await chatApi.hide(c.id); if (chats.state.current === c.id) await chats.select(null); await chats.refreshList(); }) },
  ];
}

export default function ChatWindow() {
  const s = useChats();
  const [view, setView] = useState<ListView>({ folder: "all", text: "" });
  const [dialog, setDialog] = useState<Dialog>(null);
  const [found, setFound] = useState<{ chats: ChatItem[]; messages: (SearchHit & { chat_id: string })[]; terms?: string[] } | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [jump, setJump] = useState<{ chatId: string; id: number; terms: string[] } | null>(null);
  const [searchOpen, setSearchOpen] = useState(0);
  const win = useRef<HTMLDivElement>(null);
  const menu = useContextMenu();
  const items = useMemo(() => visibleChats(s.items, view), [s.items, view]);
  const current = s.items.find((c) => c.id === s.current) ?? null;

  const ask = useCallback((title: string, body: string, label: string, run: () => Promise<void>) => setDialog({ kind: "confirm", title, body, label, run }), []);
  const close = useCallback(() => chats.closeWindow(), []);
  // окно не модальное: Esc закрывает его, только когда фокус внутри (страница под ним продолжает работать)
  useEffect(() => {
    win.current?.focus();
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !dialog && win.current?.contains(document.activeElement) && !document.querySelector(".ctx-menu, .iv-backdrop, .emoji-pop")) close();
    };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [close, dialog]);

  // ---- плавающее окно: перетаскивание за заголовки, размер — за угол, двойной щелчок по заголовку — развернуть (как у плеера записей)
  const [rect, setRect] = useState<Rect>(initialRect);
  const [maxed, setMaxed] = useState(false);
  const [view_, setVp] = useState(vp);
  useEffect(() => {
    const f = () => { setVp(vp()); setRect((r) => clampRect(r, window.innerWidth, window.innerHeight)); };
    const mq = window.matchMedia?.(`(max-width: ${MOBILE - 1}px)`);         // смена ширины надёжнее ловится и так (поворот, эмуляция устройства)
    window.addEventListener("resize", f);
    mq?.addEventListener?.("change", f);
    return () => { window.removeEventListener("resize", f); mq?.removeEventListener?.("change", f); };
  }, []);
  const mobile = view_.w < MOBILE;
  const drag = useRef<{ dx: number; dy: number } | null>(null);
  const size = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const onDown = (e: React.PointerEvent) => {
    const t = e.target as HTMLElement;
    if (mobile || maxed || e.button !== 0 || !t.closest("[data-drag]") || t.closest("button, a, input, select, textarea, label, [role=tab]")) return;
    e.preventDefault();                                      // без выделения текста при перетаскивании
    drag.current = { dx: e.clientX - rect.x, dy: e.clientY - rect.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    // значения — сейчас, а не в функции обновления: React применит её позже, когда отпускание кнопки уже обнулит ссылки (было: окно падало при быстром отпускании)
    const d = drag.current, z = size.current, x = e.clientX, y = e.clientY;
    if (d) setRect((r) => clampRect({ ...r, x: x - d.dx, y: y - d.dy }, view_.w, view_.h));
    else if (z) setRect((r) => clampRect({ ...r, w: Math.max(MIN.w, z.w + x - z.x), h: Math.max(MIN.h, z.h + y - z.y) }, view_.w, view_.h));
  };
  const onUp = (e: React.PointerEvent) => {
    if (!drag.current && !size.current) return;
    drag.current = size.current = null;
    try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* уже отпущен */ }
    saveRect(rect);
  };
  const onSize = (e: React.PointerEvent) => {
    e.stopPropagation();
    e.preventDefault();
    size.current = { x: e.clientX, y: e.clientY, w: rect.w, h: rect.h };
    win.current?.setPointerCapture(e.pointerId);
  };
  const onDouble = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    if (!mobile && t.closest("[data-drag]") && !t.closest("button, a, input, select, textarea, label, [role=tab]")) setMaxed((m) => !m);
  };
  const box = mobile ? null : maxed ? { x: 8, y: 8, w: view_.w - 16, h: view_.h - 16 } : rect;
  const onePane = mobile || (box?.w ?? 0) < ONE_PANE;

  // поиск слева: по названиям сразу, по содержимому переписок — запросом (не чаще чем раз в 300 мс)
  useEffect(() => {
    const t = view.text.trim();
    if (t.length < 2) { setFound(null); setPeople([]); return; }
    const h = window.setTimeout(() => {
      void chatApi.searchAll(t).then(setFound).catch(() => setFound(null));
      void chatApi.people(t).then((r) => setPeople(r.users.slice(0, 8))).catch(() => setPeople([]));      // сотрудники — написать сразу из поиска
    }, 300);
    return () => window.clearTimeout(h);
  }, [view.text]);

  const open = (id: string) => void chats.select(id);
  const tabs: { id: string; label: string; icon?: "bell" | "archive" }[] = [{ id: "all", label: "Все" }, { id: "unread", label: "Непрочитанные", icon: "bell" },
    ...s.folders.map((f) => ({ id: f.id, label: f.name })), { id: "archive", label: "Архив", icon: "archive" }];

  return createPortal(
    <>
      <div className={`chat-win ${s.current ? "has-current" : ""} ${onePane ? "one-pane" : ""} ${mobile ? "mobile" : ""}`} ref={win} role="dialog" aria-label="Чаты" tabIndex={-1}
           style={box ? { left: box.x, top: box.y, width: box.w, height: box.h } : undefined}
           onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onDoubleClick={onDouble}>
        <aside className="chat-side" aria-label="Список чатов">
          <div className="chat-side-head" data-drag title={mobile ? undefined : "Перетащите, чтобы переместить окно; двойной щелчок — развернуть"}>
            <h2>Чаты</h2>
            <span className={`chat-conn ${s.status}`} title={s.status === "online" ? "Связь есть" : s.status === "offline" ? "Нет связи" : "Подключение…"} aria-hidden />
            <div className="spacer" />
            <button type="button" className="btn ghost mini icon-btn" title="Новый чат" aria-label="Новый чат" onClick={() => setDialog({ kind: "new" })}><Icon name="plus" size={18} /></button>
            <button type="button" className="btn ghost mini icon-btn" title="Папки" aria-label="Управление папками" onClick={() => setDialog({ kind: "folders" })}><Icon name="folder" size={18} /></button>
            <button type="button" className="btn ghost mini icon-btn" title="Настройки чатов" aria-label="Настройки чатов" onClick={() => setDialog({ kind: "prefs" })}><Icon name="gear" size={18} /></button>
            {!mobile && <button type="button" className="btn ghost mini icon-btn" title={maxed ? "Восстановить размер" : "Развернуть"} aria-label={maxed ? "Восстановить размер окна" : "Развернуть окно"}
                                aria-pressed={maxed} onClick={() => setMaxed((m) => !m)}><Icon name="expand" size={17} /></button>}
          </div>
          <label className="chat-search">
            <Icon name="search" size={16} />
            <input type="search" placeholder="Поиск по чатам и сообщениям" aria-label="Поиск по чатам, сотрудникам и сообщениям" value={view.text}
                   onChange={(e) => setView({ ...view, text: e.target.value })} />
          </label>
          <div className="chat-tabs" role="tablist" aria-label="Папки">
            {tabs.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={view.folder === t.id} className={`chat-tab ${view.folder === t.id ? "on" : ""} ${t.icon ? "ico" : ""}`}
                      title={t.icon ? t.label : undefined} aria-label={t.icon ? t.label : undefined} onClick={() => setView({ ...view, folder: t.id })}>
                {t.icon ? <Icon name={t.icon} size={15} /> : t.label}{t.id === "unread" && s.unread ? <span className="chat-tab-n">{s.unread}</span> : null}
              </button>
            ))}
          </div>
          <ul className="chat-list" role="listbox" aria-label="Переписки">
            {items.map((c) => (
              <ChatRow key={c.id} c={c} active={c.id === s.current} me={s.config?.me.id ?? ""} presence={!!s.config?.presence} onOpen={() => open(c.id)}
                       onMenu={menu.onContextMenu(() => chatMenu(c, s.folders, { open: () => open(c.id), info: () => setDialog({ kind: "info", chatId: c.id }) }, ask))} />
            ))}
            {items.length === 0 && (
              <li className="chat-empty">
                {s.items.length === 0 ? <>Переписок пока нет.<button type="button" className="btn primary mini" onClick={() => setDialog({ kind: "new" })}>Написать сотруднику</button></>
                  : view.text ? "Ничего не найдено в названиях" : view.folder === "archive" ? "В архиве пусто" : "Здесь пока пусто"}
              </li>
            )}
            {people.length > 0 && (
              <li className="chat-found">
                <div className="chat-found-h">Сотрудники</div>
                {people.filter((p) => !s.items.some((c) => c.kind === "direct" && c.peer?.id === p.id)).map((p) => (
                  <button key={p.id ?? p.ref ?? p.name} type="button" className="chat-found-row person"
                          onClick={() => void chatApi.direct({ id: p.id, ref: p.ref, login: p.login }).then((r) => { setView({ ...view, text: "" }); return chats.openChat(r.id); }).catch((e) => alert((e as Error).message))}>
                    <Avatar name={p.name} url={p.avatar_url} size={30} /><span><b>{p.name}</b><span className="muted small">{[p.title, p.department].filter(Boolean).join(" · ") || "написать"}</span></span>
                  </button>
                ))}
              </li>
            )}
            {found && found.messages.length > 0 && (
              <li className="chat-found">
                <div className="chat-found-h">Сообщения</div>
                {found.messages.map((m) => {
                  const c = s.items.find((x) => x.id === m.chat_id);
                  return (
                    <button key={m.id} type="button" className="chat-found-row" onClick={() => { void chats.select(m.chat_id); setJump({ chatId: m.chat_id, id: m.id, terms: found.terms ?? [] }); }}>
                      <b>{c?.title ?? "Чат"}</b><span className="muted small">{m.author_name} · {listTime(m.created_at)}</span><span className="chat-found-text">{m.text}</span>
                    </button>
                  );
                })}
              </li>
            )}
          </ul>
        </aside>
        <section className="chat-main" aria-label="Переписка">
          {current
            ? <Conversation key={current.id} chat={current} onInfo={() => setDialog({ kind: "info", chatId: current.id })} ask={ask} jump={jump?.chatId === current.id ? jump : null}
                            onJumped={() => setJump(null)} searchSignal={searchOpen} />
            : (
              <div className="chat-placeholder">
                <Icon name="chat" size={40} />
                <p>Выберите переписку слева или начните новую.</p>
                <button type="button" className="btn primary" onClick={() => setDialog({ kind: "new" })}>Новый чат</button>
              </div>
            )}
        </section>
        <FloatClose corner onClick={close} label="Закрыть чаты" title="Закрыть (Esc)" />
        {!mobile && !maxed && <span className="chat-resize" onPointerDown={onSize} aria-hidden title="Изменить размер" />}
      </div>
      {menu.node}
      {dialog?.kind === "new" && <NewChatDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "folders" && <FoldersDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "prefs" && <PrefsDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "info" && <ChatInfoDialog chatId={dialog.chatId} onClose={() => setDialog(null)} onSearch={() => { setDialog(null); setSearchOpen((n) => n + 1); }} />}
      {dialog?.kind === "confirm" && <ConfirmDialog title={dialog.title} body={dialog.body} confirmLabel={dialog.label} onConfirm={dialog.run} onClose={() => setDialog(null)} />}
    </>, document.body);
}

function ChatRow({ c, active, me, presence, onOpen, onMenu }: { c: ChatItem; active: boolean; me: string; presence: boolean; onOpen: () => void; onMenu: (e: React.MouseEvent) => void }) {
  const last = c.last;
  const who = last && last.kind === "text" ? (last.author_id === me ? "Вы: " : c.kind === "group" ? `${last.author_name.split(" ")[0]}: ` : "") : "";
  return (
    <li role="option" aria-selected={active}>
      <button type="button" className={`chat-row ${active ? "on" : ""} ${c.unread && !c.muted ? "unread" : ""}`} onClick={onOpen} onContextMenu={onMenu}>
        <span className="chat-av">
          <Avatar name={c.title} url={c.avatar_url} size={42} />
          {presence && c.peer?.online && <span className="chat-online" title="В сети" aria-label="в сети" />}
        </span>
        <span className="chat-row-main">
          <span className="chat-row-top">
            <span className="chat-row-title">{c.kind === "group" && <Icon name="users" size={13} />}{c.title}</span>
            {c.pinned && <Icon name="pin" size={13} className="chat-row-ico" aria-label="закреплён" />}
            {c.muted && <Icon name="bellOff" size={13} className="chat-row-ico" aria-label="без уведомлений" />}
            <span className="chat-row-time">{last ? listTime(last.at) : ""}</span>
          </span>
          <span className="chat-row-bottom">
            <span className="chat-row-last">{last ? (last.deleted ? <i>Сообщение удалено</i> : <>{who}{plainText(last.text) || "Вложение"}</>) : <i>Нет сообщений</i>}</span>
            <span className={`chat-row-n ${c.muted ? "muted" : ""}`} hidden={!c.unread} aria-label={`непрочитанных: ${c.unread}`}>{c.unread > 999 ? "999+" : c.unread}</span>
          </span>
        </span>
      </button>
    </li>
  );
}
