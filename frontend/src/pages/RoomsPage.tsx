import { type CSSProperties, FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError, type Room, type TempRoomPolicy } from "../api";
import { useSite } from "../site";
import { Icon } from "../components/Icons";
import { useContextMenu, type MenuItem } from "../components/ContextMenu";
import { useToast } from "../components/Toast";
import { copyText } from "../util";
import { Modal } from "../components/Dialogs";
import { initials, magnet, spotlight, tint } from "../fx";

/** «Создать временную переговорку»: только название; остальное — значения по умолчанию. Создатель становится руководителем. */
function TempRoomDialog({ policy, onClose }: { policy: TempRoomPolicy; onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const create = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { const r = await api.createTemporaryRoom(name.trim()); onClose(); navigate(`/rooms/${r.slug}`); }
    catch (x) { setErr((x as ApiError).message); setBusy(false); }
  };
  return (
    <Modal title="Временная переговорка" onClose={onClose}>
      <form className="form" onSubmit={(e) => void create(e)}>
        <label>Название встречи<input autoFocus value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Например: Разбор инцидента" />
          <span className="help">Комната существует, пока идёт встреча, и закрывается сама через {policy.grace_minutes} мин после выхода всех. Запись, стенограмма, чат и протоколы остаются в «Истории». Вы станете руководителем комнаты и сможете пригласить коллег.</span></label>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row"><button className="btn primary" disabled={busy || !name.trim()}>{busy ? "Создание…" : "Создать"}</button>
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Отмена</button></div>
      </form>
    </Modal>
  );
}

type Filter = "all" | "live" | "free" | "temp";
const FILTERS: [Filter, string][] = [["all", "Все"], ["live", "Идёт встреча"], ["free", "Свободные"], ["temp", "Временные"]];

type View = "tiles" | "list";
const VIEW_KEY = "pg:roomsView";
const roomUrl = (r: Room) => `${window.location.origin}/rooms/${r.slug}`;
const guestUrl = (r: Room) => `${window.location.origin}/guest/${r.guest_token}`;

/** Кнопки копирования ссылок. Обычная (зелёная) — для сотрудников; гостевая (красная) — только если гостевой вход включён и пользователь вправе ею делиться. */
type Toast = (text: string, tone?: "ok" | "error") => void;

/** Пункты меню правой кнопки у комнаты (все они есть и обычным путём: карточка, кнопки копирования). «Настройки комнаты» — только администратору. */
function useRoomMenu(r: Room, onCopy: Toast, isAdmin: boolean) {
  const navigate = useNavigate();
  const { onContextMenu, node, openAt } = useContextMenu();
  const copy = async (url: string, what: string) => { const ok = await copyText(url); onCopy(ok ? `${what} скопирована` : "Не удалось скопировать — выделите ссылку вручную", ok ? "ok" : "error"); };
  const items = (): MenuItem[] => [
    { id: "enter", label: "Войти", icon: "arrowR", onSelect: () => navigate(`/rooms/${r.slug}`) },
    { id: "link", label: "Скопировать ссылку", icon: "copy", onSelect: () => void copy(roomUrl(r), "Ссылка") },
    { id: "guest", label: "Скопировать гостевую ссылку", icon: "copy", hidden: !r.guest_token, onSelect: () => void copy(guestUrl(r), "Гостевая ссылка") },
    { id: "tab", label: "Открыть в новой вкладке", icon: "arrowR", onSelect: () => { window.open(roomUrl(r), "_blank", "noopener"); } },
    { id: "settings", label: "Настройки комнаты", icon: "gear", hidden: !isAdmin, onSelect: () => { try { sessionStorage.setItem("adminTab", "rooms"); } catch { /* вкладка не запомнится */ } navigate("/admin"); } },
  ];
  /** Кнопка «⋯»: то же меню, что и по правой кнопке, у самой кнопки (доступно с клавиатуры и на сенсорных экранах). */
  const openFrom = (el: HTMLElement) => { const b = el.getBoundingClientRect(); openAt(Math.round(b.right), Math.round(b.bottom + 4), items()); };
  return { handler: onContextMenu(items), node, openFrom };
}

function CopyLinks({ r, onCopy, compact = true }: { r: Room; onCopy: (text: string, tone?: "ok" | "error") => void; compact?: boolean }) {
  const copy = async (url: string, what: string) => { const ok = await copyText(url); onCopy(ok ? `${what} скопирована` : "Не удалось скопировать — выделите ссылку вручную", ok ? "ok" : "error"); };
  return (
    <span className={`rc-actions ${compact ? "" : "inline"}`}>
      <button type="button" className="copybtn reg" onClick={() => void copy(roomUrl(r), "Ссылка для сотрудников")}
              title="Ссылка для сотрудников: вход по учётной записи" aria-label={`Скопировать ссылку для сотрудников: ${r.name}`}><Icon name="copy" size={15} /></button>
      {r.guest_token && (
        <button type="button" className="copybtn guest" onClick={() => void copy(guestUrl(r), "Гостевая ссылка")}
                title="Гостевая ссылка: вход без учётной записи, по приглашению" aria-label={`Скопировать гостевую ссылку: ${r.name}`}><Icon name="copy" size={15} /></button>
      )}
    </span>
  );
}

/** Компактная карточка комнаты для режима «Компактно»: несколько колонок, длинное название — в две строки с подсказкой, вся карточка открывает комнату. */
function RoomMini({ r, i, onCopy, isAdmin }: { r: Room; i: number; onCopy: Toast; isAdmin: boolean }) {
  const menu = useRoomMenu(r, onCopy, isAdmin);
  const live = r.active_meeting;
  const full = !!live && live.participants >= r.max_participants;
  const navigate = useNavigate();
  const open = () => navigate(`/rooms/${r.slug}`);
  return (
    <div className="rm" role="listitem" onContextMenu={menu.handler} style={{ "--i": i } as CSSProperties} tabIndex={0} onClick={open}
         onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); open(); } }}>
      <div className="rm-top">
        <span className="rm-name" title={r.description ? `${r.name} — ${r.description}` : r.name}>{r.name}</span>
        <span className={`pill ${live ? "live" : "free"}`} title={live ? `Идёт встреча, участников: ${live.participants}` : "Свободна"}><i className="pulse" aria-hidden />{live ? live.participants : "Свободна"}</span>
      </div>
      <div className="rm-sub small muted">
        <code title={`Технический идентификатор (адрес комнаты): ${r.slug}`}>{r.slug}</code>
        <span title={r.has_password ? "Для входа нужен пароль" : undefined}>{full ? "мест нет" : `до ${r.max_participants}`}{r.has_password ? " · 🔒" : ""}</span>
      </div>
      {menu.node}
      <div className="rm-act" onClick={(e) => e.stopPropagation()}>
        <CopyLinks r={r} onCopy={onCopy} compact={false} />
        <span className="spacer" />
        <button type="button" className="rm-more" aria-label={`Действия: ${r.name}`} title="Действия (или правая кнопка мыши)" aria-haspopup="menu" onClick={(e) => menu.openFrom(e.currentTarget)}><Icon name="more" size={16} /></button>
        <Link to={`/rooms/${r.slug}`} className="btn mini primary">{full ? "Мест нет" : "Войти"}</Link>
      </div>
    </div>
  );
}

function RoomCard({ r, i, onCopy, isAdmin }: { r: Room; i: number; onCopy: Toast; isAdmin: boolean }) {
  const menu = useRoomMenu(r, onCopy, isAdmin);
  const t = tint(r.name);
  const live = r.active_meeting;
  const full = live && live.participants >= r.max_participants;
  return (
    <div className="rc-cell" style={{ "--i": i } as CSSProperties} onContextMenu={menu.handler}>
    {menu.node}
    <CopyLinks r={r} onCopy={onCopy} />
    <Link to={`/rooms/${r.slug}`} className={`room-card rc ${live ? "is-live" : ""}`} style={{ "--i": i, "--ha": t.a, "--hb": t.b } as CSSProperties} onPointerMove={spotlight}
          aria-label={`${r.name}: ${live ? `идёт встреча, участников ${live.participants}` : "свободна"}`}>
      <span className="rc-glow" aria-hidden />
      <div className="rc-top">
        <span className="rc-mark" aria-hidden>{initials(r.name)}</span>
        <div className="rc-title">
          <h2 title={r.name}>{r.name}</h2>
          <span className={`pill ${live ? "live" : "free"}`}>
            <i className="pulse" aria-hidden />{live ? `Идёт встреча · ${live.participants} уч.` : "Свободна"}
          </span>
          {r.has_password && <span className="tag" title="Для входа нужен пароль"><Icon name="lock" size={12} /> Пароль</span>}
        </div>
      </div>
      {r.description && <p className="rc-desc">{r.description}</p>}
      <div className="rc-tags">
        {r.lifetime === "temporary" && <span className="tag" title={r.lifecycle === "grace_period" ? "Все вышли: комната закроется, если никто не вернётся" : "Комната на одну встречу"}>{r.lifecycle === "grace_period" ? "Временная · ждёт возврата" : "Временная"}</span>}
        {r.room_type === "presentation" && <span className="tag" title="Участники слушают; говорят руководители и те, кому дали слово"><Icon name="screen" size={13} /> Презентация</span>}
        {r.transcription_enabled && <span className="tag" title="Реплики записываются в стенограмму"><Icon name="transcript" size={13} /> Стенограмма</span>}
        {r.auto_record ? <span className="tag warn" title="Запись звука начинается вместе со встречей"><Icon name="record" size={13} /> Запись сразу</span>
          : r.record_audio && <span className="tag warn" title="Руководитель может включить запись звука"><Icon name="record" size={13} /> Запись</span>}
      </div>
      <div className="rc-foot">
        <span className="rc-cap"><Icon name="users" size={14} /> {live ? `${live.participants} из ${r.max_participants}` : `до ${r.max_participants}`}</span>
        <span className="rc-go">{full ? "Мест нет" : live ? "Присоединиться" : "Войти"} <Icon name="arrowR" size={16} /></span>
      </div>
    </Link>
    </div>
  );
}

export default function RoomsPage({ isAdmin = false }: { isAdmin?: boolean }) {
  const site = useSite();
  const [rooms, setRooms] = useState<Room[] | null>(null);
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<TempRoomPolicy | null>(null);
  const [tempOpen, setTempOpen] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [view, setView] = useState<View>(() => { try { return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "tiles"; } catch { return "tiles"; } });
  const [toast, showToast] = useToast();
  const pickView = (v: View) => { setView(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* хранилище недоступно — выбор не запомнится */ } };

  useEffect(() => { void api.temporaryPolicy().then(setPolicy).catch(() => undefined); }, [tempOpen]);
  useEffect(() => {
    const load = () => api.rooms().then(setRooms).catch((e) => setError(e.message));
    load();
    const t = window.setInterval(load, 10000); // «кто сейчас в комнате» обновляется без перезагрузки
    return () => window.clearInterval(t);
  }, []);

  const shown = useMemo(() => (rooms ?? []).filter((r) => {
    if (q.trim() && !`${r.name} ${r.description ?? ""}`.toLowerCase().includes(q.trim().toLowerCase())) return false;
    if (filter === "live") return !!r.active_meeting;
    if (filter === "free") return !r.active_meeting;
    if (filter === "temp") return r.lifetime === "temporary";
    return true;
  }), [rooms, q, filter]);

  if (error) return <div className="alert error">{error}</div>;
  if (!rooms) return <div className="rooms-page"><div className="grid rooms-grid" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="room-card rc skeleton" style={{ "--i": i } as CSSProperties} />)}</div></div>;

  const liveCount = rooms.filter((r) => r.active_meeting).length;
  const canTemp = !!policy?.enabled;
  return (
    <section className="rooms-page">
      <header className="rooms-hero">
        <div>
          <p className="eyebrow">{site.org.short || site.name}</p>
          <h1>Переговорки</h1>
          <p className="lead">
            {rooms.length === 0 ? "Для вас пока нет доступных комнат." : liveCount > 0 ? `Сейчас идут встречи: ${liveCount}. Выберите комнату, чтобы присоединиться или начать новую.` : "Выберите комнату, чтобы начать встречу. Речь будет записана в стенограмму и станет протоколом."}
          </p>
        </div>
        {canTemp && (
          <button className="btn primary cta" {...magnet} onClick={() => setTempOpen(true)} disabled={!policy?.can_create}
                  title={policy?.can_create ? "Комната на одну встречу: закрывается сама, материалы остаются в «Истории»" : `Уже ${policy?.active_mine} активных временных переговорок (предел ${policy?.max_per_user})`}>
            <Icon name="sparkle" size={17} /> Временная переговорка
            <small className="cta-note">закроется после встречи</small>
          </button>
        )}
      </header>

      {rooms.length > 0 && (
        <div className="rooms-toolbar">
          <label className="search"><Icon name="search" size={16} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти комнату" aria-label="Найти комнату" /></label>
          <div className="seg viewseg" role="group" aria-label="Вид списка">
            <button type="button" aria-pressed={view === "tiles"} onClick={() => pickView("tiles")} title="Плитки"><Icon name="grid" size={15} /> Плитка</button>
            <button type="button" aria-pressed={view === "list"} onClick={() => pickView("list")} title="Компактные карточки в несколько колонок"><Icon name="list" size={15} /> Компактно</button>
          </div>
          <div className="chips" role="group" aria-label="Фильтр комнат">
            {FILTERS.map(([id, label]) => <button key={id} type="button" className={`chip ${filter === id ? "on" : ""}`} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>)}
          </div>
        </div>
      )}

      {rooms.length === 0 && (
        <div className="empty">
          <div className="empty-art" aria-hidden><Icon name="users" size={34} /></div>
          <h2>Доступных комнат нет</h2>
          <p className="muted">Обратитесь к администратору, чтобы вам открыли доступ{canTemp ? ", либо создайте временную переговорку для быстрой встречи" : ""}.</p>
        </div>
      )}
      {rooms.length > 0 && shown.length === 0 && <p className="muted">По этому запросу комнат нет. Измените поиск или фильтр.</p>}
      {view === "tiles"
        ? <div className="grid rooms-grid">{shown.map((r, i) => <RoomCard key={r.id} r={r} i={i} onCopy={showToast} isAdmin={isAdmin} />)}</div>
        : <div className="rooms-compact" role="list">{shown.map((r, i) => <RoomMini key={r.id} r={r} i={i} onCopy={showToast} isAdmin={isAdmin} />)}</div>}
      {toast}
      {tempOpen && policy && <TempRoomDialog policy={policy} onClose={() => setTempOpen(false)} />}
    </section>
  );
}
