import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Avatar from "../components/Avatar";
import { useContextMenu, type MenuItem } from "../components/ContextMenu";
import { ConfirmDialog } from "../components/Dialogs";
import { Icon } from "../components/Icons";
import { chatApi, type ChatItem, type SearchHit } from "./chatApi";
import { ChatInfoDialog, FoldersDialog, NewChatDialog, PrefsDialog } from "./ChatDialogs";
import { listTime, plainText, visibleChats, type ListView } from "./chatModel";
import { chats, useChats } from "./chatStore";
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
  const [jump, setJump] = useState<{ chatId: string; id: number; terms: string[] } | null>(null);
  const [searchOpen, setSearchOpen] = useState(0);
  const win = useRef<HTMLDivElement>(null);
  const menu = useContextMenu();
  const items = useMemo(() => visibleChats(s.items, view), [s.items, view]);
  const current = s.items.find((c) => c.id === s.current) ?? null;

  const ask = useCallback((title: string, body: string, label: string, run: () => Promise<void>) => setDialog({ kind: "confirm", title, body, label, run }), []);
  const close = useCallback(() => chats.closeWindow(), []);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    win.current?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !dialog && !document.querySelector(".ctx-menu, .iv-backdrop")) close(); };
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("keydown", esc); prev?.focus?.(); };
  }, [close, dialog]);

  // поиск слева: по названиям сразу, по содержимому переписок — запросом (не чаще чем раз в 300 мс)
  useEffect(() => {
    const t = view.text.trim();
    if (t.length < 2) { setFound(null); return; }
    const h = window.setTimeout(() => void chatApi.searchAll(t).then(setFound).catch(() => setFound(null)), 300);
    return () => window.clearTimeout(h);
  }, [view.text]);

  const open = (id: string) => void chats.select(id);
  const tabs: { id: string; label: string }[] = [{ id: "all", label: "Все" }, { id: "unread", label: "Непрочитанные" }, ...s.folders.map((f) => ({ id: f.id, label: f.name })), { id: "archive", label: "Архив" }];

  return createPortal(
    <div className="chat-back" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className={`chat-win ${s.current ? "has-current" : ""}`} ref={win} role="dialog" aria-modal="true" aria-label="Чаты" tabIndex={-1}>
        <aside className="chat-side" aria-label="Список чатов">
          <div className="chat-side-head">
            <h2>Чаты</h2>
            <span className={`chat-conn ${s.status}`} title={s.status === "online" ? "Связь есть" : s.status === "offline" ? "Нет связи" : "Подключение…"} aria-hidden />
            <div className="spacer" />
            <button type="button" className="btn ghost mini icon-btn" title="Новый чат" aria-label="Новый чат" onClick={() => setDialog({ kind: "new" })}><Icon name="plus" size={18} /></button>
            <button type="button" className="btn ghost mini icon-btn" title="Папки" aria-label="Управление папками" onClick={() => setDialog({ kind: "folders" })}><Icon name="folder" size={18} /></button>
            <button type="button" className="btn ghost mini icon-btn" title="Настройки чатов" aria-label="Настройки чатов" onClick={() => setDialog({ kind: "prefs" })}><Icon name="gear" size={18} /></button>
            <button type="button" className="btn ghost mini icon-btn" title="Закрыть (Esc)" aria-label="Закрыть чаты" onClick={close}><Icon name="close" size={18} /></button>
          </div>
          <label className="chat-search">
            <Icon name="search" size={16} />
            <input type="search" placeholder="Поиск по чатам и сообщениям" aria-label="Поиск по чатам, сотрудникам и сообщениям" value={view.text}
                   onChange={(e) => setView({ ...view, text: e.target.value })} />
          </label>
          <div className="chat-tabs" role="tablist" aria-label="Папки">
            {tabs.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={view.folder === t.id} className={`chat-tab ${view.folder === t.id ? "on" : ""}`}
                      onClick={() => setView({ ...view, folder: t.id })}>{t.label}{t.id === "unread" && s.unread ? <span className="chat-tab-n">{s.unread}</span> : null}</button>
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
      </div>
      {menu.node}
      {dialog?.kind === "new" && <NewChatDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "folders" && <FoldersDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "prefs" && <PrefsDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === "info" && <ChatInfoDialog chatId={dialog.chatId} onClose={() => setDialog(null)} onSearch={() => { setDialog(null); setSearchOpen((n) => n + 1); }} />}
      {dialog?.kind === "confirm" && <ConfirmDialog title={dialog.title} body={dialog.body} confirmLabel={dialog.label} onConfirm={dialog.run} onClose={() => setDialog(null)} />}
    </div>, document.body);
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
