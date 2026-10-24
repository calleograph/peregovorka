// Состояние чатов вкладки: список переписок, открытые ленты, непрочитанные, папки, сокет событий, присутствие, уведомления.
// Один экземпляр на вкладку (useChats). Сокет — тот же /api/v1/ws, личный канал (`subscribe_user`); после обрыва — переподключение
// с нарастающей паузой и догрузка пропущенного по REST (повторы отбрасываются по id).
import { useSyncExternalStore } from "react";
import { backoffDelay } from "../liveSocket";
import { playChatSound } from "../chatSound";
import { chatApi, newClientId, type ChatConfig, type ChatFileInfo, type ChatFolder, type ChatItem, type ChatMsg } from "./chatApi";
import { mentionsMe, mergeMessages, onNewMessage, preview, totalUnread } from "./chatModel";

export interface Conv { messages: ChatMsg[]; hasMoreBefore: boolean; loading: boolean; loaded: boolean; error: string; typing: Record<string, { name: string; until: number }>; reads: Record<string, number>; version: number }
export interface Toast { id: number; chatId: string | null; title: string; text: string }
export interface ChatState {
  ready: boolean; enabled: boolean; config: ChatConfig | null; items: ChatItem[]; unread: number; folders: ChatFolder[];
  open: boolean; current: string | null; convs: Record<string, Conv>; status: "connecting" | "online" | "reconnecting" | "offline"; toasts: Toast[]; listVersion: number;
}

const SS_OPEN = "pg:chat:open";            // окно открыто и выбранная переписка — на время сеанса вкладки (переживает перезагрузку страницы)
const ss = { get: () => { try { return JSON.parse(sessionStorage.getItem(SS_OPEN) || "null") as { current: string | null } | null; } catch { return null; } },
  set: (v: { current: string | null } | null) => { try { if (v) sessionStorage.setItem(SS_OPEN, JSON.stringify(v)); else sessionStorage.removeItem(SS_OPEN); } catch { /* недоступно */ } } };
const EMPTY_CONV: Conv = { messages: [], hasMoreBefore: true, loading: false, loaded: false, error: "", typing: {}, reads: {}, version: 0 };
const PING_MS = 30000;
const ACTIVE_MS = 3 * 60000;                 // «в сети» — было действие пользователя за последние 3 минуты и вкладка видна

class ChatStore {
  state: ChatState = { ready: false, enabled: false, config: null, items: [], unread: 0, folders: [], open: false, current: null, convs: {}, status: "connecting", toasts: [], listVersion: 0 };
  private subs = new Set<() => void>();
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: number | undefined;
  private pinger: number | undefined;
  private started = false;
  private stopped = false;
  private lastActivity = Date.now();
  private listTimer: number | undefined;
  private readTimer: Record<string, number> = {};
  private toastSeq = 0;
  private lastSound = 0;

  subscribe = (fn: () => void) => { this.subs.add(fn); return () => { this.subs.delete(fn); }; };
  get = () => this.state;
  private set(p: Partial<ChatState>) { this.state = { ...this.state, ...p }; for (const fn of [...this.subs]) fn(); }
  private conv(id: string): Conv { return this.state.convs[id] ?? EMPTY_CONV; }
  private setConv(id: string, p: Partial<Conv>) { this.set({ convs: { ...this.state.convs, [id]: { ...this.conv(id), ...p } } }); }

  /** Запуск после входа: настройки, список, сокет. Повторный вызов ничего не делает. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.stopped = false;
    try {
      const config = await chatApi.config();
      this.set({ config, enabled: config.enabled, ready: true });
      if (!config.enabled) return;
    } catch { this.set({ ready: true, enabled: false }); return; }
    await Promise.all([this.refreshList(), this.refreshFolders()]);
    for (const ev of ["pointerdown", "keydown", "wheel", "touchstart"]) window.addEventListener(ev, this.touch, { passive: true });
    document.addEventListener("visibilitychange", this.onVisible);
    window.addEventListener("online", this.retryNow);
    this.connect();
    const was = ss.get();
    if (was) this.openWindow(was.current && this.state.items.some((c) => c.id === was.current) ? was.current : null);
  }

  stop(): void {
    this.stopped = true;
    this.started = false;
    window.clearTimeout(this.timer);
    window.clearInterval(this.pinger);
    try { this.ws?.close(1000); } catch { /* уже закрыт */ }
    this.ws = null;
    for (const ev of ["pointerdown", "keydown", "wheel", "touchstart"]) window.removeEventListener(ev, this.touch);
    document.removeEventListener("visibilitychange", this.onVisible);
    window.removeEventListener("online", this.retryNow);
    this.set({ items: [], unread: 0, convs: {}, open: false, current: null, ready: false });
  }

  private touch = () => {
    const was = Date.now() - this.lastActivity > ACTIVE_MS;
    this.lastActivity = Date.now();
    if (was) this.presence();
  };
  private onVisible = () => {
    if (document.visibilityState === "visible") { this.touch(); if (this.state.current && this.state.open) this.scheduleRead(this.state.current); }
  };
  private retryNow = () => { if (!this.ws && !this.stopped) { window.clearTimeout(this.timer); this.connect(); } };

  // ------------------------------------------------------------------------------------------ сокет
  private connect(): void {
    if (this.stopped) return;
    this.set({ status: this.attempt ? "reconnecting" : "connecting" });
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket;
    try { ws = new WebSocket(`${proto}://${location.host}/api/v1/ws`); } catch { this.schedule(); return; }
    this.ws = ws;
    ws.onopen = () => { ws.send(JSON.stringify({ type: "subscribe_user" })); };
    ws.onmessage = (e) => {
      let ev: Record<string, unknown>;
      try { ev = JSON.parse(String(e.data)); } catch { return; }
      if (ev.type === "subscribed_user") {
        const resync = this.attempt > 0;
        this.attempt = 0;
        this.set({ status: "online" });
        window.clearInterval(this.pinger);
        this.pinger = window.setInterval(() => { this.wsSend({ type: "ping" }); this.presence(); }, PING_MS);
        this.presence();
        if (resync) void this.resync();
        return;
      }
      this.onEvent(ev);
    };
    ws.onclose = (e) => {
      if (this.ws !== ws) return;
      this.ws = null;
      window.clearInterval(this.pinger);
      if (this.stopped || e.code === 4401 || e.code === 4403) { this.set({ status: "offline" }); return; }
      this.schedule();
    };
  }

  private schedule(): void {
    this.attempt += 1;
    this.set({ status: "reconnecting" });
    this.timer = window.setTimeout(() => this.connect(), backoffDelay(this.attempt - 1));
  }

  private wsSend(o: object): void { try { if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(o)); } catch { /* переподключится */ } }

  /** Присутствие: только при видимой вкладке и недавнем действии — действующая, но неактивная сессия «в сети» не делает. */
  private presence(): void {
    if (document.visibilityState === "visible" && Date.now() - this.lastActivity < ACTIVE_MS) this.wsSend({ type: "presence", active: true });
  }

  /** После переподключения: список заново, в загруженных лентах — всё новее последнего известного сообщения. */
  private async resync(): Promise<void> {
    await this.refreshList();
    for (const [id, c] of Object.entries(this.state.convs)) {
      if (!c.loaded) continue;
      let after = Math.max(0, ...c.messages.filter((m) => !m.pending).map((m) => m.id));
      for (let i = 0; i < 20; i++) {
        let page;
        try { page = await chatApi.messages(id, { after_id: after, limit: 200 }); } catch { break; }
        this.setConv(id, { messages: mergeMessages(this.conv(id).messages, page.messages) });
        if (!page.has_more_after || !page.messages.length) break;
        after = page.messages[page.messages.length - 1].id;
      }
    }
  }

  private viewing(id: string): boolean { return this.state.open && this.state.current === id && document.visibilityState === "visible"; }

  private onEvent(ev: Record<string, unknown>): void {
    const chatId = typeof ev.chat_id === "string" ? ev.chat_id : null;
    const myId = this.state.config?.me.id ?? "";
    switch (ev.type) {
      case "chat_message": {
        const m = ev.message as ChatMsg;
        if (!chatId) return;
        if (this.state.convs[chatId]?.loaded) this.setConv(chatId, { messages: mergeMessages(this.conv(chatId).messages, [m]) });
        const viewing = this.viewing(chatId);
        const next = onNewMessage(this.state.items, chatId, m, myId, viewing);
        if (next) this.set({ items: next, unread: totalUnread(next) }); else this.refreshListSoon();
        const item = this.state.items.find((c) => c.id === chatId);
        if (m.author_id !== myId && m.kind === "text") {
          if (viewing) this.scheduleRead(chatId);
          else if (!item?.muted || mentionsMe(m.text, this.state.config?.me.name ?? "")) {      // упоминание приходит и из чата без уведомлений
            this.notify(chatId, item ? (item.kind === "group" ? `${item.title} · ${m.author_name}` : item.title) : m.author_name, preview(m));
          }
        }
        const t = { ...this.conv(chatId).typing };
        if (m.author_id && t[m.author_id]) { delete t[m.author_id]; this.setConv(chatId, { typing: t }); }
        return;
      }
      case "chat_message_updated": {
        const m = ev.message as ChatMsg;
        if (chatId && this.state.convs[chatId]?.loaded) this.setConv(chatId, { messages: mergeMessages(this.conv(chatId).messages, [m]) });
        if (chatId && this.state.items.find((c) => c.id === chatId)?.last?.id === m.id) this.refreshListSoon();
        return;
      }
      case "chat_read": {
        if (!chatId) return;
        const uid = String(ev.user_id), last = Number(ev.last_read_id);
        if (uid === myId) {                                    // прочитано на другом устройстве этой учётной записи
          const items = this.state.items.map((c) => (c.id === chatId && last >= (c.last?.id ?? 0) ? { ...c, unread: 0, last_read_id: last } : c));
          this.set({ items, unread: totalUnread(items) });
          if (items.find((c) => c.id === chatId)?.unread) this.refreshListSoon();
        } else this.setConv(chatId, { reads: { ...this.conv(chatId).reads, [uid]: Math.max(last, this.conv(chatId).reads[uid] ?? 0) } });
        return;
      }
      case "chat_typing": {
        if (!chatId || ev.user_id === myId) return;
        this.setConv(chatId, { typing: { ...this.conv(chatId).typing, [String(ev.user_id)]: { name: String(ev.name), until: Date.now() + 4000 } } });
        window.setTimeout(() => {
          const t = { ...this.conv(chatId).typing };
          const cur = t[String(ev.user_id)];
          if (cur && cur.until <= Date.now()) { delete t[String(ev.user_id)]; this.setConv(chatId, { typing: t }); }
        }, 4200);
        return;
      }
      case "chat_updated":
        this.refreshListSoon();
        if (chatId && this.state.convs[chatId]) this.setConv(chatId, { version: this.conv(chatId).version + 1 });
        return;
      case "chat_cleared":
        if (chatId) this.setConv(chatId, { messages: [], hasMoreBefore: false, version: this.conv(chatId).version + 1 });
        this.refreshListSoon();
        return;
      case "chat_folders": void this.refreshFolders(); return;
      case "chat_prefs": if (this.state.config) this.set({ config: { ...this.state.config, prefs: ev.prefs as ChatConfig["prefs"] } }); return;
      case "chat_presence_online": this.notify(null, String(ev.name), "Теперь в сети"); this.refreshListSoon(); return;
    }
  }

  // ------------------------------------------------------------------------------------------ уведомления
  private notify(chatId: string | null, title: string, text: string): void {
    const id = ++this.toastSeq;
    this.set({ toasts: [...this.state.toasts.slice(-2), { id, chatId, title, text }] });
    window.setTimeout(() => this.dismiss(id), 6000);
    // звук — по личной настройке чатов (на всех устройствах), серия сообщений даёт один сигнал
    if (chatId && this.state.config?.prefs.sound && Date.now() - this.lastSound > 1500) { this.lastSound = Date.now(); try { playChatSound(true); } catch { /* звук недоступен */ } }
  }
  dismiss = (id: number) => this.set({ toasts: this.state.toasts.filter((t) => t.id !== id) });

  // ------------------------------------------------------------------------------------------ список и папки
  async refreshList(): Promise<void> {
    try {
      const r = await chatApi.list();
      this.set({ items: r.items, unread: totalUnread(r.items), listVersion: this.state.listVersion + 1 });
    } catch { /* сеть: покажется при следующей попытке */ }
  }
  private refreshListSoon(): void { window.clearTimeout(this.listTimer); this.listTimer = window.setTimeout(() => void this.refreshList(), 250); }
  async refreshFolders(): Promise<void> { try { this.set({ folders: (await chatApi.folders()).items }); } catch { /* позже */ } }
  async reloadConfig(): Promise<void> { try { this.set({ config: await chatApi.config() }); } catch { /* позже */ } }

  // ------------------------------------------------------------------------------------------ окно и переписка
  openWindow(chatId?: string | null): void {
    this.set({ open: true, current: chatId === undefined ? this.state.current : chatId });
    ss.set({ current: this.state.current });
    if (this.state.current) void this.select(this.state.current);
  }
  closeWindow(): void { this.set({ open: false }); ss.set(null); }

  async select(id: string | null): Promise<void> {
    this.set({ current: id });
    if (this.state.open) ss.set({ current: id });
    if (!id) return;
    const c = this.conv(id);
    if (!c.loaded && !c.loading) await this.loadLatest(id);
    else this.scheduleRead(id);
  }

  async loadLatest(id: string): Promise<void> {
    this.setConv(id, { loading: true, error: "" });
    try {
      const p = await chatApi.messages(id, { limit: 50 });
      this.setConv(id, { messages: mergeMessages(this.conv(id).messages.filter((m) => m.pending), p.messages), hasMoreBefore: !!p.has_more_before, loading: false, loaded: true,
        reads: { ...(p.reads ?? {}), ...this.conv(id).reads } });
      this.scheduleRead(id);
    } catch (e) { this.setConv(id, { loading: false, error: (e as Error).message || "Не удалось загрузить переписку" }); }
  }

  /** Более ранние сообщения (прокрутка вверх). */
  async loadOlder(id: string): Promise<number> {
    const c = this.conv(id);
    const first = c.messages.find((m) => !m.pending);
    if (c.loading || !c.hasMoreBefore || !first) return 0;
    this.setConv(id, { loading: true });
    try {
      const p = await chatApi.messages(id, { before_id: first.id, limit: 50 });
      this.setConv(id, { messages: mergeMessages(this.conv(id).messages, p.messages), hasMoreBefore: !!p.has_more_before, loading: false });
      return p.messages.length;
    } catch { this.setConv(id, { loading: false }); return 0; }
  }

  /** Переход к найденному сообщению: окно вокруг него (лента заменяется окном, дальше догружается по прокрутке). */
  async jumpTo(id: string, messageId: number): Promise<void> {
    if (this.conv(id).messages.some((m) => m.id === messageId)) return;
    const p = await chatApi.messages(id, { around_id: messageId, limit: 80 });
    this.setConv(id, { messages: p.messages, hasMoreBefore: !!p.has_more_before, loaded: true, version: this.conv(id).version + 1 });
    if (p.has_more_after) {                                  // догрузить до конца, чтобы новые сообщения встали на место
      let after = p.messages[p.messages.length - 1]?.id ?? messageId;
      for (let i = 0; i < 10; i++) {
        const more = await chatApi.messages(id, { after_id: after, limit: 200 });
        this.setConv(id, { messages: mergeMessages(this.conv(id).messages, more.messages) });
        if (!more.has_more_after || !more.messages.length) break;
        after = more.messages[more.messages.length - 1].id;
      }
    }
  }

  scheduleRead(id: string): void {
    if (!this.viewing(id)) return;
    window.clearTimeout(this.readTimer[id]);
    this.readTimer[id] = window.setTimeout(() => void this.markRead(id), 400);
  }

  private async markRead(id: string): Promise<void> {
    const last = Math.max(0, ...this.conv(id).messages.filter((m) => !m.pending).map((m) => m.id));
    const item = this.state.items.find((c) => c.id === id);
    if (!last || (item && item.last_read_id >= last && item.unread === 0)) return;
    const items = this.state.items.map((c) => (c.id === id ? { ...c, unread: 0, last_read_id: Math.max(c.last_read_id, last) } : c));
    this.set({ items, unread: totalUnread(items) });
    try { await chatApi.read(id, last); } catch { /* повторится при следующем просмотре */ }
  }

  /** Отправка: сообщение видно сразу; повтор (кнопка «Повторить» или сбой сети) — с тем же client_id, сервер дубль не создаст. */
  async send(id: string, text: string, opts: { replyTo?: ChatMsg | null; files?: ChatFileInfo[]; clientId?: string } = {}): Promise<void> {
    const me = this.state.config?.me;
    const client_id = opts.clientId ?? newClientId();
    const local: ChatMsg = { id: 0, chat_id: id, author_id: me?.id ?? null, author_name: me?.name ?? "", kind: "text", text, created_at: new Date().toISOString(), edited_at: null,
      deleted: false, client_id, forwarded: null, files: opts.files ?? [], pending: "sending",
      reply_to: opts.replyTo ? { id: opts.replyTo.id, author_name: opts.replyTo.author_name, text: opts.replyTo.text.slice(0, 160), deleted: false } : null };
    this.setConv(id, { messages: mergeMessages(this.conv(id).messages, [local]) });
    for (let attempt = 0; ; attempt++) {
      try {
        const m = await chatApi.send(id, { text, client_id, reply_to: opts.replyTo?.id ?? null, files: (opts.files ?? []).map((f) => f.id) });
        this.setConv(id, { messages: mergeMessages(this.conv(id).messages, [m]) });
        const next = onNewMessage(this.state.items, id, m, me?.id ?? "", true);
        if (next) this.set({ items: next, unread: totalUnread(next) }); else this.refreshListSoon();
        return;
      } catch (e) {
        const status = (e as { status?: number }).status ?? 0;
        if (status === 0 && attempt < 3) { await new Promise((r) => window.setTimeout(r, 800 * (attempt + 1))); continue; }   // сеть: тот же client_id
        this.setConv(id, { messages: mergeMessages(this.conv(id).messages, [{ ...local, pending: "failed" }]) });
        throw e;
      }
    }
  }

  retry(id: string, m: ChatMsg): Promise<void> {
    return this.send(id, m.text, { clientId: m.client_id ?? undefined, files: m.files, replyTo: m.reply_to ? ({ ...m, id: m.reply_to.id, author_name: m.reply_to.author_name, text: m.reply_to.text } as ChatMsg) : null });
  }
  discard(id: string, m: ChatMsg): void { this.setConv(id, { messages: this.conv(id).messages.filter((x) => !(x.pending && x.client_id === m.client_id)) }); }

  applyMessage(id: string, m: ChatMsg): void { this.setConv(id, { messages: mergeMessages(this.conv(id).messages, [m]) }); }

  /** Новая или существующая переписка открыта из поиска сотрудников. */
  async openChat(id: string): Promise<void> {
    await this.refreshList();
    this.openWindow(id);
  }
}

export const chats = new ChatStore();

export function useChats(): ChatState {
  return useSyncExternalStore(chats.subscribe, chats.get, chats.get);
}
