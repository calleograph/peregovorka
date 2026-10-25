import { type CSSProperties, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError, type PeerPolicy, type Room, type TempRoomPolicy, type UpcomingMeeting } from "../api";
import { useSite } from "../site";
import { Icon } from "../components/Icons";
import { useContextMenu, type MenuItem } from "../components/ContextMenu";
import { useToast } from "../components/Toast";
import { copyText } from "../util";
import { Modal } from "../components/Dialogs";
import { PeerCreateDialog, PeerManageDialog } from "../components/PeerRooms";
import { magnet, spotlight, tint } from "../fx";
import RoomIcon from "../components/RoomIcon";
import UpcomingSection from "../components/UpcomingMeetings";
import { meetingState, span, tickMs } from "../upcomingStatus";

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

type Filter = "all" | "upcoming" | "permanent" | "live" | "free";
const FILTERS: [Filter, string][] = [["all", "Все"], ["upcoming", "Предстоящие"], ["permanent", "Постоянные"], ["live", "Идёт встреча"], ["free", "Свободные"]];
const UPCOMING_POLL_MS = 30_000;        // сверка с сервером; отсчёт между сверками считает браузер

type View = "tiles" | "list";
const VIEW_KEY = "pg:roomsView";
const roomUrl = (r: Room) => r.room_type === "peer" ? `${window.location.origin}/p/${r.slug.replace(/^p-/, "")}` : `${window.location.origin}/rooms/${r.slug}`;
const guestUrl = (r: Room) => `${window.location.origin}/guest/${r.guest_token}`;

/** Кнопки копирования ссылок. Обычная (зелёная) — для сотрудников; гостевая (красная) — только если гостевой вход включён и пользователь вправе ею делиться. */
type Toast = (text: string, tone?: "ok" | "error") => void;

/** Пункты меню правой кнопки у комнаты (все они есть и обычным путём: карточка, кнопки копирования). «Настройки комнаты» — только администратору. */
function useRoomMenu(r: Room, onCopy: Toast, isAdmin: boolean, onManagePeer?: (id: string) => void) {
  const navigate = useNavigate();
  const { onContextMenu, node, openAt } = useContextMenu();
  const copy = async (url: string, what: string) => { const ok = await copyText(url); onCopy(ok ? `${what} скопирована` : "Не удалось скопировать — выделите ссылку вручную", ok ? "ok" : "error"); };
  const items = (): MenuItem[] => [
    { id: "enter", label: "Войти", icon: "arrowR", onSelect: () => navigate(`/rooms/${r.slug}`) },
    { id: "link", label: "Скопировать ссылку", icon: "copy", onSelect: () => void copy(roomUrl(r), "Ссылка") },
    { id: "guest", label: "Скопировать гостевую ссылку", icon: "copy", hidden: !r.guest_token, onSelect: () => void copy(guestUrl(r), "Гостевая ссылка") },
    { id: "peer", label: "Управление ссылкой…", icon: "gear", hidden: r.room_type !== "peer" || !onManagePeer, onSelect: () => onManagePeer?.(r.id) },
    { id: "tab", label: "Открыть в новой вкладке", icon: "arrowR", onSelect: () => { window.open(roomUrl(r), "_blank", "noopener"); } },
    { id: "settings", label: "Настройки комнаты", icon: "gear", hidden: !isAdmin, onSelect: () => { try { sessionStorage.setItem("adminTab", "rooms"); } catch { /* вкладка не запомнится */ } navigate("/admin"); } },
  ];
  /** Кнопка «⋯»: то же меню, что и по правой кнопке, у самой кнопки (доступно с клавиатуры и на сенсорных экранах). */
  const openFrom = (el: HTMLElement) => { const b = el.getBoundingClientRect(); openAt(Math.round(b.right), Math.round(b.bottom + 4), items()); };
  return { handler: onContextMenu(items), node, openFrom };
}

/** Вторичные действия комнаты: копировать ссылку для сотрудников и меню «⋯» (гостевая ссылка, управление, настройки — там, отдельно и с подписями). */
function RoomActions({ r, onCopy, menu }: { r: Room; onCopy: Toast; menu: { openFrom: (el: HTMLElement) => void } }) {
  const copy = async () => { const ok = await copyText(roomUrl(r)); onCopy(ok ? "Ссылка для сотрудников скопирована" : "Не удалось скопировать — выделите ссылку вручную", ok ? "ok" : "error"); };
  return (
    <span className="rc-actions">
      <button type="button" className="iconbtn" onClick={() => void copy()} title="Скопировать ссылку для сотрудников (вход по учётной записи)" aria-label={`Скопировать ссылку для сотрудников: ${r.name}`}><Icon name="copy" size={16} /></button>
      <button type="button" className="iconbtn rm-more" aria-label={`Действия: ${r.name}`} title="Другие действия (или правая кнопка мыши)" aria-haspopup="menu" onClick={(e) => menu.openFrom(e.currentTarget)}><Icon name="more" size={16} /></button>
    </span>
  );
}

/** Возможности и особенности комнаты — одним набором для плитки и списка. */
function RoomTags({ r }: { r: Room }) {
  return (
    <>
      {r.lifetime === "temporary" && <span className="tag" title={r.lifecycle === "grace_period" ? "Все вышли: комната закроется, если никто не вернётся" : "Комната на одну встречу"}>{r.lifecycle === "grace_period" ? "Временная · ждёт возврата" : "Временная"}</span>}
      {r.room_type === "peer" && <span className="tag" title="Равноправная комната: ведущего нет, у всех участников равные права">Равноправная</span>}
      {r.room_type === "presentation" && <span className="tag" title="Участники слушают; говорят руководители и те, кому дали слово"><Icon name="screen" size={13} /> Презентация</span>}
      {r.transcription_enabled && <span className="tag" title="Реплики записываются в стенограмму"><Icon name="transcript" size={13} /> Стенограмма</span>}
      {r.auto_record ? <span className="tag warn" title="Запись звука начинается вместе со встречей"><Icon name="record" size={13} /> Запись сразу</span>
        : r.record_audio && <span className="tag warn" title="Руководитель может включить запись звука"><Icon name="record" size={13} /> Запись</span>}
    </>
  );
}

/** Строка списка: те же данные, что в плитке, в одну плотную строку. Вся строка открывает комнату; кнопки справа — отдельно, без перекрытий. */
function RoomMini({ r, i, onCopy, isAdmin, onManagePeer }: { r: Room; i: number; onCopy: Toast; isAdmin: boolean; onManagePeer?: (id: string) => void }) {
  const menu = useRoomMenu(r, onCopy, isAdmin, onManagePeer);
  const live = r.active_meeting;
  const full = !!live && live.participants >= r.max_participants;
  const navigate = useNavigate();
  const open = () => navigate(`/rooms/${r.slug}`);
  return (
    <div className={`rm ${live ? "is-live" : ""}`} role="listitem" onContextMenu={menu.handler} style={{ "--i": i } as CSSProperties} tabIndex={0} onClick={open}
         onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); open(); } }}>
      <RoomIcon name={r.name} icon={r.icon} size={38} className="rm-mark" />
      <div className="rm-main">
        <span className="rm-name" title={r.description ? `${r.name} — ${r.description}` : r.name}>{r.name}{r.has_password && <Icon name="lock" size={13} />}</span>
        {r.description && <span className="rm-desc">{r.description}</span>}
      </div>
      <div className="rm-tags"><RoomTags r={r} /></div>
      <span className={`pill ${live ? "live" : "free"}`} title={live ? `Идёт встреча, участников: ${live.participants}` : "Свободна"}><i className="pulse" aria-hidden />{live ? `Идёт встреча · ${live.participants}` : "Свободна"}</span>
      <span className="rm-cap" title="Предел участников"><Icon name="users" size={14} /> {full ? "мест нет" : `до ${r.max_participants}`}</span>
      {menu.node}
      <div className="rm-act" onClick={(e) => e.stopPropagation()}>
        <RoomActions r={r} onCopy={onCopy} menu={menu} />
        <Link to={`/rooms/${r.slug}`} className="btn mini go">{full ? "Мест нет" : live ? "Присоединиться" : "Войти"} <Icon name="arrowR" size={14} /></Link>
      </div>
    </div>
  );
}

function RoomCard({ r, i, onCopy, isAdmin, onManagePeer }: { r: Room; i: number; onCopy: Toast; isAdmin: boolean; onManagePeer?: (id: string) => void }) {
  const menu = useRoomMenu(r, onCopy, isAdmin, onManagePeer);
  const t = tint(r.name);
  const live = r.active_meeting;
  const full = live && live.participants >= r.max_participants;
  return (
    <div className="rc-cell" style={{ "--i": i } as CSSProperties} onContextMenu={menu.handler}>
    {menu.node}
    <RoomActions r={r} onCopy={onCopy} menu={menu} />
    <Link to={`/rooms/${r.slug}`} className={`room-card rc ${live ? "is-live" : ""}`} style={{ "--i": i, "--ha": t.a, "--hb": t.b } as CSSProperties} onPointerMove={spotlight}
          aria-label={`${r.name}: ${live ? `идёт встреча, участников ${live.participants}` : "свободна"}`}>
      <span className="rc-glow" aria-hidden />
      <div className="rc-top">
        <RoomIcon name={r.name} icon={r.icon} size={46} className="rc-mark" />
        <div className="rc-title">
          <h2 title={r.name}>{r.name}</h2>
          <span className={`pill ${live ? "live" : "free"}`}>
            <i className="pulse" aria-hidden />{live ? `Идёт встреча · ${live.participants} уч.` : "Свободна"}
          </span>
          {r.has_password && <span className="tag" title="Для входа нужен пароль"><Icon name="lock" size={12} /> Пароль</span>}
        </div>
      </div>
      {r.description && <p className="rc-desc">{r.description}</p>}
      <div className="rc-tags"><RoomTags r={r} /></div>
      <div className="rc-foot">
        <span className="rc-cap"><Icon name="users" size={14} /> {live ? `${live.participants} из ${r.max_participants}` : `до ${r.max_participants}`}</span>
        <span className="rc-go btn mini go" aria-hidden>{full ? "Мест нет" : live ? "Присоединиться" : "Войти"} <Icon name="arrowR" size={15} /></span>
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
  const [peerPolicy, setPeerPolicy] = useState<PeerPolicy | null>(null);
  const [peerOpen, setPeerOpen] = useState(false);
  const [peerManage, setPeerManage] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [view, setView] = useState<View>(() => { try { return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "tiles"; } catch { return "tiles"; } });
  const [toast, showToast] = useToast();
  const [upcoming, setUpcoming] = useState<UpcomingMeeting[]>([]);
  const searchRef = useRef<HTMLInputElement>(null);
  const skew = useRef(0);                                   // часы сервера минус часы браузера: отсчёт не зависит от неточных часов компьютера
  const [now, setNow] = useState(() => Date.now());
  const [mineOnly, setMineOnly] = useState(true);           // администратор: по умолчанию только свои встречи
  const pickView = (v: View) => { setView(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* хранилище недоступно — выбор не запомнится */ } };

  useEffect(() => { void api.temporaryPolicy().then(setPolicy).catch(() => undefined); }, [tempOpen]);
  useEffect(() => { void api.peer.policy().then(setPeerPolicy).catch(() => undefined); }, [peerOpen]);
  useEffect(() => {
    const load = () => api.rooms().then(setRooms).catch((e) => setError(e.message));
    load();
    const t = window.setInterval(load, 10000); // «кто сейчас в комнате» обновляется без перезагрузки
    return () => window.clearInterval(t);
  }, []);
  useEffect(() => {
    let alive = true;
    const load = () => api.upcoming().then((r) => {
      if (!alive) return;
      skew.current = new Date(r.server_time).getTime() - Date.now();
      setUpcoming(r.items); setNow(Date.now() + skew.current);
    }).catch(() => undefined);                                     // встречи недоступны — страница работает с постоянными комнатами
    load();
    const t = window.setInterval(load, UPCOMING_POLL_MS);
    const vis = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { alive = false; window.clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, []);
  useEffect(() => {                                                // отсчёт: раз в секунду у самой границы начала, иначе раз в 15 с
    const t = window.setTimeout(() => setNow(Date.now() + skew.current), tickMs(upcoming, now));
    return () => window.clearTimeout(t);
  }, [upcoming, now]);

  const shown = useMemo(() => (rooms ?? []).filter((r) => {
    if (filter === "upcoming") return false;
    if (q.trim() && !`${r.name} ${r.description ?? ""}`.toLowerCase().includes(q.trim().toLowerCase())) return false;
    if (filter === "live") return !!r.active_meeting;
    if (filter === "free") return !r.active_meeting;
    return true;
  }), [rooms, q, filter]);
  useEffect(() => {                                                // Ctrl+K (⌘K) — в строку поиска
    const k = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); searchRef.current?.focus(); searchRef.current?.select(); } };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  const counts: Record<Filter, number> = {
    all: rooms?.length ?? 0, upcoming: upcoming.length, permanent: rooms?.length ?? 0,
    live: (rooms ?? []).filter((r) => r.active_meeting).length, free: (rooms ?? []).filter((r) => !r.active_meeting).length,
  };
  const adminView = isAdmin && upcoming.some((m) => !m.mine);
  const meetings = useMemo(() => upcoming.filter((m) => {
    if (adminView && mineOnly && !m.mine) return false;
    if (filter === "permanent" || filter === "free") return false;
    if (filter === "live" && !m.live) return false;
    return !q.trim() || `${m.name} ${m.organizer ?? ""}`.toLowerCase().includes(q.trim().toLowerCase());
  }), [upcoming, adminView, mineOnly, filter, q]);
  const next = upcoming.find((m) => m.mine && !m.cancelled && !m.live && m.kind === "scheduled" && !!m.end && new Date(m.start).getTime() > now);

  if (error) return <div className="alert error">{error}</div>;
  if (!rooms) return <div className="rooms-page"><div className="grid rooms-grid" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="room-card rc skeleton" style={{ "--i": i } as CSSProperties} />)}</div></div>;

  const liveCount = rooms.filter((r) => r.active_meeting).length;
  const canTemp = !!policy?.enabled;
  const anything = rooms.length > 0 || upcoming.length > 0;
  const showUpcoming = meetings.length > 0 || (adminView && mineOnly && filter !== "permanent" && filter !== "free" && !q.trim());    // у администратора — хотя бы переключатель «Все встречи организации»
  return (
    <section className="rooms-page">
      <header className="rooms-hero">
        <div>
          <p className="eyebrow">{site.org.short || site.name}</p>
          <h1>Переговорки</h1>
          <p className="lead">
            {next && meetingState(next, now).state !== "ended" ? <>Ближайшая встреча — «{next.name}» через {span(new Date(next.start).getTime() - now)}. </> : null}
            {!anything ? "Для вас пока нет доступных комнат." : liveCount > 0 ? `Сейчас идут встречи: ${liveCount}. Выберите комнату, чтобы присоединиться или начать новую.` : "Выберите комнату, чтобы начать встречу. Речь будет записана в стенограмму и станет протоколом."}
          </p>
        </div>
        {peerPolicy?.enabled && (
          <button className="btn cta" {...magnet} onClick={() => setPeerOpen(true)} disabled={!peerPolicy.can_create}
                  title={peerPolicy.can_create ? "Комната без ведущего: у всех участников равные права; открывается по ссылке" : `Уже ${peerPolicy.active_mine} активных равноправных комнат — закройте одну из них`}>
            <Icon name="users" size={17} /> Равноправная комната
            <small className="cta-note">по ссылке, без ведущего</small>
          </button>
        )}
        {canTemp && (
          <button className="btn primary cta" {...magnet} onClick={() => setTempOpen(true)} disabled={!policy?.can_create}
                  title={policy?.can_create ? "Комната на одну встречу: закрывается сама, материалы остаются в «Истории»" : `Уже ${policy?.active_mine} активных временных переговорок (предел ${policy?.max_per_user})`}>
            <Icon name="sparkle" size={17} /> Временная переговорка
            <small className="cta-note">закроется после встречи</small>
          </button>
        )}
      </header>

      {anything && (
        <div className="rooms-toolbar">
          <label className="search"><Icon name="search" size={18} /><input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти встречу или комнату…" aria-label="Найти встречу или комнату" /><kbd aria-hidden>Ctrl K</kbd></label>
          <div className="seg viewseg" role="group" aria-label="Вид списка">
            <button type="button" aria-pressed={view === "tiles"} onClick={() => pickView("tiles")} title="Плитки"><Icon name="grid" size={15} /> Плитка</button>
            <button type="button" aria-pressed={view === "list"} onClick={() => pickView("list")} title="Список: те же данные в одну строку"><Icon name="list" size={15} /> Список</button>
          </div>
          <div className="chips" role="group" aria-label="Фильтр комнат">
            {FILTERS.map(([id, label]) => <button key={id} type="button" className={`chip ${filter === id ? "on" : ""}`} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}<span className="n">{counts[id]}</span></button>)}
          </div>
        </div>
      )}

      {showUpcoming && <UpcomingSection items={meetings} now={now} view={view} mineOnly={adminView ? mineOnly : null} onMineOnly={setMineOnly}
                                         othersCount={adminView ? upcoming.filter((m) => !m.mine).length : 0} actions={{ onCopy: showToast, onManagePeer: setPeerManage }} />}
      {showUpcoming && filter !== "upcoming" && rooms.length > 0 && <div className="rooms-section-h"><h2>Постоянные переговорки <span className="muted">{shown.length}</span></h2></div>}
      {!anything && (
        <div className="empty">
          <div className="empty-art" aria-hidden><Icon name="users" size={34} /></div>
          <h2>Доступных комнат нет</h2>
          <p className="muted">Обратитесь к администратору, чтобы вам открыли доступ{canTemp ? ", либо создайте временную переговорку для быстрой встречи" : ""}.</p>
        </div>
      )}
      {anything && shown.length === 0 && !showUpcoming && <p className="muted">По этому запросу ничего нет. Измените поиск или фильтр.</p>}
      {rooms.length === 0 && upcoming.length > 0 && filter !== "upcoming" && <p className="muted small">Постоянных переговорок для вас нет.</p>}
      {view === "tiles"
        ? <div className="grid rooms-grid">{shown.map((r, i) => <RoomCard key={r.id} r={r} i={i} onCopy={showToast} isAdmin={isAdmin} onManagePeer={setPeerManage} />)}</div>
        : <div className="rooms-rows" role="list">{shown.map((r, i) => <RoomMini key={r.id} r={r} i={i} onCopy={showToast} isAdmin={isAdmin} onManagePeer={setPeerManage} />)}</div>}
      {toast}
      {tempOpen && policy && <TempRoomDialog policy={policy} onClose={() => setTempOpen(false)} />}
      {peerOpen && peerPolicy && <PeerCreateDialog policy={peerPolicy} onClose={() => setPeerOpen(false)} />}
      {peerManage && <PeerManageDialog roomId={peerManage} onClose={() => setPeerManage(null)} onChanged={() => void api.upcoming().then((r) => setUpcoming(r.items)).catch(() => undefined)} />}
    </section>
  );
}
