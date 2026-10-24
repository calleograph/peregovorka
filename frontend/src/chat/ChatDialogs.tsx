import { useEffect, useMemo, useRef, useState } from "react";
import Avatar from "../components/Avatar";
import { ConfirmDialog, Modal } from "../components/Dialogs";
import { Icon } from "../components/Icons";
import { chatApi, newClientId, type ChatInfo, type ChatMsg, type PeopleResult, type Person, type Receipt } from "./chatApi";
import { visibleChats } from "./chatModel";
import { chats, useChats } from "./chatStore";

const key = (p: Person) => p.id ?? `ref:${p.ref}`;
const pick = (p: Person) => ({ id: p.id, ref: p.ref, login: p.login });

/** Поиск сотрудников: внутренний справочник (кто уже входил) и корпоративный каталог; группы каталога раскрываются в участников. */
function PeoplePicker({ selected, onToggle, multi }: { selected: Person[]; onToggle: (p: Person) => void; multi: boolean }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<PeopleResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const h = window.setTimeout(() => {
      setBusy(true); setErr("");
      chatApi.people(q.trim()).then(setRes).catch((e) => setErr((e as Error).message)).finally(() => setBusy(false));
    }, 250);
    return () => window.clearTimeout(h);
  }, [q]);
  const chosen = new Set(selected.map(key));
  const addGroup = async (ref: string) => {
    try { for (const p of (await chatApi.groupMembers(ref)).users) if (!chosen.has(key(p))) onToggle(p); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="people">
      <label className="chat-search"><Icon name="search" size={16} />
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="ФИО, логин, почта или подразделение" aria-label="Поиск сотрудников" />
      </label>
      {res?.directory === "not_configured" && <p className="muted small">Корпоративный каталог не подключён — поиск по сотрудникам, которые уже входили в Peregovorka.</p>}
      {res?.directory === "unavailable" && <p className="alert warn small">Каталог сейчас недоступен — показаны только сотрудники, которые уже входили.</p>}
      {err && <p className="alert error small">{err}</p>}
      <ul className="people-list" aria-busy={busy}>
        {res?.users.map((p) => (
          <li key={key(p)}>
            <button type="button" className={`people-row ${chosen.has(key(p)) ? "on" : ""}`} onClick={() => onToggle(p)} aria-pressed={multi ? chosen.has(key(p)) : undefined}>
              <Avatar name={p.name} url={p.avatar_url} size={34} />
              <span><b>{p.name}</b><span className="muted small">{[p.title, p.department, p.login].filter(Boolean).join(" · ")}{p.source === "directory" ? " · из каталога" : ""}</span></span>
              {multi && <span className="people-check" aria-hidden>{chosen.has(key(p)) ? <Icon name="check" size={16} /> : null}</span>}
            </button>
          </li>
        ))}
        {multi && res?.groups.map((g) => (
          <li key={g.ref}>
            <button type="button" className="people-row group" onClick={() => void addGroup(g.ref)}>
              <span className="people-gi"><Icon name="users" size={18} /></span>
              <span><b>{g.name}</b><span className="muted small">группа каталога — добавить всех участников{g.description ? ` · ${g.description}` : ""}</span></span>
            </button>
          </li>
        ))}
        {res && !res.users.length && !res.groups.length && <li className="muted small people-none">Никого не нашлось. Попробуйте фамилию или логин.</li>}
      </ul>
    </div>
  );
}

export function NewChatDialog({ onClose }: { onClose: () => void }) {
  const s = useChats();
  const [mode, setMode] = useState<"direct" | "group">("direct");
  const [sel, setSel] = useState<Person[]>([]);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const openDirect = async (p: Person) => {
    setBusy(true); setErr("");
    try { const r = await chatApi.direct(pick(p)); onClose(); await chats.openChat(r.id); } catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  const createGroup = async () => {
    setBusy(true); setErr("");
    try { const r = await chatApi.createGroup(title.trim(), sel.map(pick)); onClose(); await chats.openChat(r.id); } catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  const toggle = (p: Person) => (mode === "direct" ? void openDirect(p) : setSel((x) => (x.some((y) => key(y) === key(p)) ? x.filter((y) => key(y) !== key(p)) : [...x, p])));
  const max = s.config?.max_group_members ?? 300;
  return (
    <Modal title="Новый чат" onClose={onClose}>
      <div className="seg" role="tablist">
        <button type="button" role="tab" aria-selected={mode === "direct"} className={mode === "direct" ? "on" : ""} onClick={() => setMode("direct")}>Личный</button>
        <button type="button" role="tab" aria-selected={mode === "group"} className={mode === "group" ? "on" : ""} onClick={() => setMode("group")}>Групповой</button>
      </div>
      {mode === "group" && (
        <>
          <label>Название группы<input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="Например, «Проект Альфа»" /></label>
          {sel.length > 0 && (
            <div className="people-chips">
              {sel.map((p) => <span key={key(p)} className="chip on">{p.name}<button type="button" aria-label={`Убрать ${p.name}`} onClick={() => toggle(p)}>×</button></span>)}
            </div>
          )}
        </>
      )}
      <PeoplePicker selected={sel} onToggle={toggle} multi={mode === "group"} />
      {err && <div className="alert error">{err}</div>}
      {mode === "group" && (
        <div className="row">
          <button type="button" className="btn primary" disabled={busy || !title.trim() || !sel.length || sel.length + 1 > max} onClick={() => void createGroup()}>
            {busy ? "Создаём…" : `Создать группу (${sel.length + 1})`}
          </button>
          <button type="button" className="btn ghost" onClick={onClose}>Отмена</button>
        </div>
      )}
      {mode === "direct" && <p className="muted small">Если переписка с сотрудником уже есть, откроется она — новой не будет.</p>}
    </Modal>
  );
}

export function FoldersDialog({ onClose }: { onClose: () => void }) {
  const s = useChats();
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const [del, setDel] = useState<{ id: string; name: string } | null>(null);
  const run = async (fn: () => Promise<unknown>) => { setErr(""); try { await fn(); await chats.refreshFolders(); } catch (e) { setErr((e as Error).message); } };
  const counts = useMemo(() => Object.fromEntries(s.folders.map((f) => [f.id, s.items.filter((c) => c.folder_id === f.id).length])), [s.folders, s.items]);
  return (
    <Modal title="Папки" onClose={onClose}>
      <p className="muted small">Папки — только ваши: у собеседников порядок переписок не меняется. Удаление папки переписки не удаляет.</p>
      <ul className="folders">
        {s.folders.map((f) => (
          <li key={f.id}>
            <Icon name="folder" size={16} />
            <input defaultValue={f.name} maxLength={60} aria-label={`Название папки ${f.name}`}
                   onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== f.name) void run(() => chatApi.renameFolder(f.id, v)); }} />
            <span className="muted small">{counts[f.id] ?? 0}</span>
            <button type="button" className="btn ghost mini icon-btn" aria-label={`Удалить папку ${f.name}`} onClick={() => setDel(f)}><Icon name="trash" size={15} /></button>
          </li>
        ))}
      </ul>
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) void run(() => chatApi.createFolder(name.trim())).then(() => setName("")); }}>
        <input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder="Новая папка: «Работа», «Проекты»…" aria-label="Название новой папки" />
        <button type="submit" className="btn primary" disabled={!name.trim()}>Создать</button>
      </form>
      {err && <div className="alert error">{err}</div>}
      {del && <ConfirmDialog title="Удалить папку?" body={`Папка «${del.name}» будет удалена, переписки из неё вернутся в общий список.`} confirmLabel="Удалить"
                             onConfirm={() => run(() => chatApi.deleteFolder(del.id)).then(() => chats.refreshList())} onClose={() => setDel(null)} />}
    </Modal>
  );
}

export function PrefsDialog({ onClose }: { onClose: () => void }) {
  const s = useChats();
  const p = s.config?.prefs ?? { presence: true, sound: true };
  const [err, setErr] = useState("");
  const set = async (patch: Partial<typeof p>) => { setErr(""); try { await chatApi.prefs(patch); await chats.reloadConfig(); } catch (e) { setErr((e as Error).message); } };
  const Row = ({ icon, title, hint, on, onChange }: { icon: "volume" | "eye"; title: string; hint: string; on: boolean; onChange: (v: boolean) => void }) => (
    <label className="pref-row">
      <span className="pi"><Icon name={icon} size={17} /></span>
      <span className="pt"><b>{title}</b><span>{hint}</span></span>
      <span className="switch"><input type="checkbox" role="switch" checked={on} onChange={(e) => onChange(e.target.checked)} /><i /></span>
    </label>
  );
  return (
    <Modal title="Настройки чатов" onClose={onClose}>
      <div className="pref-rows">
        <Row icon="volume" title="Звук новых сообщений" hint="Один сигнал на серию сообщений" on={p.sound} onChange={(v) => void set({ sound: v })} />
        {s.config?.presence && <Row icon="eye" title="Показывать, что я в сети" hint="Выключено — вас не видно «в сети», уведомление о появлении никому не придёт" on={p.presence} onChange={(v) => void set({ presence: v })} />}
      </div>
      <p className="pref-note"><Icon name="info" size={14} />Без уведомлений для отдельного чата — правой кнопкой по нему или «⋯» в переписке. Действует на всех ваших устройствах.</p>
      {err && <div className="alert error">{err}</div>}
    </Modal>
  );
}

export function ChatInfoDialog({ chatId, onClose, onSearch }: { chatId: string; onClose: () => void; onSearch: () => void }) {
  const s = useChats();
  const me = s.config?.me.id ?? "";
  const [info, setInfo] = useState<ChatInfo | null>(null);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState<Person[] | null>(null);
  const [ask, setAsk] = useState<{ title: string; body: string; label: string; run: () => Promise<void> } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const load = () => chatApi.info(chatId).then(setInfo).catch((e) => setErr((e as Error).message));
  useEffect(() => { void load(); }, [chatId]);  // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (fn: () => Promise<unknown>) => { setErr(""); try { await fn(); await load(); await chats.refreshList(); } catch (e) { setErr((e as Error).message); } };
  if (!info) return <Modal title="Информация" onClose={onClose}>{err ? <div className="alert error">{err}</div> : <p className="muted">Загрузка…</p>}</Modal>;
  const admin = info.kind === "group" && info.my_role === "admin";
  const peer = info.kind === "direct" ? info.members.find((m) => m.id !== me) : null;
  return (
    <Modal title={info.kind === "group" ? "Группа" : "Собеседник"} onClose={onClose} wide>
      <div className="chat-info-head">
        <Avatar name={info.title ?? peer?.name ?? ""} url={info.kind === "group" ? info.avatar_url : peer?.avatar_url} size={64} />
        <div>
          {admin ? <input className="chat-info-title" defaultValue={info.title ?? ""} maxLength={200} aria-label="Название группы"
                          onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== info.title) void run(() => chatApi.rename(chatId, v)); }} />
            : <h3>{info.title ?? peer?.name}</h3>}
          {peer && <p className="muted">{[peer.title, peer.department].filter(Boolean).join(" · ")}{peer.email ? ` · ${peer.email}` : ""}{s.config?.presence && peer.online ? " · в сети" : ""}</p>}
          {info.kind === "group" && <p className="muted small">Участников: {info.members.length} · создана {new Date(info.created_at).toLocaleDateString("ru-RU")}</p>}
          <div className="row">
            <button type="button" className="btn mini" onClick={onSearch}><Icon name="search" size={14} /> Поиск по сообщениям</button>
            {admin && <button type="button" className="btn mini" onClick={() => file.current?.click()}>Изображение группы…</button>}
            {admin && info.avatar_url && <button type="button" className="btn mini ghost" onClick={() => void run(() => chatApi.dropAvatar(chatId))}>Убрать изображение</button>}
            {peer && s.config?.presence && <button type="button" className="btn mini" onClick={() => void run(() => chatApi.watch(peer.id, !peer.watch))}>
              <Icon name="eye" size={14} /> {peer.watch ? "Не сообщать о появлении" : "Сообщить, когда появится в сети"}</button>}
          </div>
          <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void run(() => chatApi.setAvatar(chatId, f)); e.target.value = ""; }} />
        </div>
      </div>
      {info.kind === "group" && (
        <>
          <h4>Участники</h4>
          <ul className="members">
            {info.members.map((m) => (
              <li key={m.id}>
                <span className="chat-av"><Avatar name={m.name} url={m.avatar_url} size={32} />{s.config?.presence && m.online && <span className="chat-online" aria-label="в сети" />}</span>
                <span className="grow"><b>{m.name}{m.id === me ? " (вы)" : ""}</b><span className="muted small">{[m.title, m.department].filter(Boolean).join(" · ")}</span></span>
                {m.role === "admin" && <span className="chip">администратор</span>}
                {admin && m.id !== me && (
                  <>
                    <button type="button" className="btn ghost mini" onClick={() => void run(() => chatApi.setRole(chatId, m.id, m.role === "admin" ? "member" : "admin"))}>
                      {m.role === "admin" ? "Снять администратора" : "Сделать администратором"}</button>
                    <button type="button" className="btn ghost mini icon-btn" aria-label={`Исключить ${m.name}`}
                            onClick={() => setAsk({ title: "Исключить участника?", body: `${m.name} потеряет доступ к переписке группы.`, label: "Исключить", run: () => run(() => chatApi.removeMember(chatId, m.id)) })}>
                      <Icon name="userx" size={15} /></button>
                  </>
                )}
              </li>
            ))}
          </ul>
          <div className="row">
            {admin && adding === null && <button type="button" className="btn" onClick={() => setAdding([])}><Icon name="plus" size={15} /> Добавить участников</button>}
            <button type="button" className="btn ghost danger-text" onClick={() => setAsk({ title: "Выйти из группы?", body: "Переписка группы станет вам недоступна. Вернуть может администратор группы.", label: "Выйти",
              run: async () => { await chatApi.removeMember(chatId, me); onClose(); await chats.select(null); await chats.refreshList(); } })}>Выйти из группы</button>
          </div>
          {adding !== null && (
            <div className="chat-add">
              <PeoplePicker multi selected={adding} onToggle={(p) => setAdding((x) => (x!.some((y) => key(y) === key(p)) ? x!.filter((y) => key(y) !== key(p)) : [...x!, p]))} />
              <div className="row">
                <button type="button" className="btn primary" disabled={!adding.length} onClick={() => void run(() => chatApi.addMembers(chatId, adding.map(pick))).then(() => setAdding(null))}>Добавить ({adding.length})</button>
                <button type="button" className="btn ghost" onClick={() => setAdding(null)}>Отмена</button>
              </div>
            </div>
          )}
        </>
      )}
      {err && <div className="alert error">{err}</div>}
      {ask && <ConfirmDialog title={ask.title} body={ask.body} confirmLabel={ask.label} onConfirm={ask.run} onClose={() => setAsk(null)} />}
    </Modal>
  );
}

export function ForwardDialog({ from, msg, onClose }: { from: string; msg: ChatMsg; onClose: () => void }) {
  const s = useChats();
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const items = visibleChats(s.items, { folder: "all", text: q });
  const go = async (to: string) => {
    setErr("");
    try { await chatApi.send(to, { text: "", client_id: newClientId(), forward: { chat_id: from, message_id: msg.id } }); setDone(to); await chats.refreshList(); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal title="Переслать сообщение" onClose={onClose}>
      <blockquote className="fwd-preview">{msg.text.slice(0, 300)}</blockquote>
      {msg.files.length > 0 && <p className="muted small">Вложения не пересылаются: у получателей нет доступа к исходной переписке.</p>}
      <label className="chat-search"><Icon name="search" size={16} /><input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Кому переслать" aria-label="Поиск переписки" /></label>
      <ul className="people-list">
        {items.slice(0, 40).map((c) => (
          <li key={c.id}><button type="button" className="people-row" disabled={!!done} onClick={() => void go(c.id)}>
            <Avatar name={c.title} url={c.avatar_url} size={30} /><span><b>{c.title}</b></span>{done === c.id && <Icon name="check" size={16} />}</button></li>
        ))}
      </ul>
      {err && <div className="alert error">{err}</div>}
      {done && <div className="row"><button type="button" className="btn primary" onClick={() => { onClose(); void chats.select(done); }}>Перейти к переписке</button>
        <button type="button" className="btn ghost" onClick={onClose}>Закрыть</button></div>}
    </Modal>
  );
}

export function ReceiptsDialog({ chatId, msg, onClose }: { chatId: string; msg: ChatMsg; onClose: () => void }) {
  const [items, setItems] = useState<Receipt[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { chatApi.receipts(chatId, msg.id).then((r) => setItems(r.items)).catch((e) => setErr((e as Error).message)); }, [chatId, msg.id]);
  return (
    <Modal title="Доставка и прочтение" onClose={onClose}>
      <p className="muted small">Отправлено {new Date(msg.created_at).toLocaleString("ru-RU")}. Доставлено на сервер; ниже — кто уже открыл переписку после этого сообщения.</p>
      {err && <div className="alert error">{err}</div>}
      <ul className="members">
        {items?.map((r) => <li key={r.user_id}><span className="grow">{r.name}</span><span className={r.read ? "ok-text" : "muted"}>{r.read ? "прочитано" : "не прочитано"}</span></li>)}
      </ul>
    </Modal>
  );
}
