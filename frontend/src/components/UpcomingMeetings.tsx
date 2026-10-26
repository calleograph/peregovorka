import { type CSSProperties, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError, type Invitee, type UpcomingMeeting } from "../api";
import { action, hhmm, meetingState, STATE_LABEL, whenLabel } from "../upcomingStatus";
import { copyText } from "../util";
import { useContextMenu, type MenuItem } from "./ContextMenu";
import { Icon } from "./Icons";
import { LinkCopy } from "./LinkCopy";

type Toast = (text: string, tone?: "ok" | "error") => void;
export interface MeetingActions { onCopy: Toast; onManagePeer?: (roomId: string) => void; onIssued?: () => void }

/** Меню встречи (правая кнопка или «⋯»): те же действия, что были у временных комнат в каталоге — ссылка, гостевая ссылка, управление ссылкой равноправной комнаты. */
function useMeetingMenu(m: UpcomingMeeting, { onCopy, onManagePeer }: MeetingActions) {
  const navigate = useNavigate();
  const { onContextMenu, node, openAt } = useContextMenu();
  const full = window.location.origin + m.url;
  const copy = async (url: string, what: string) => { const ok = await copyText(url); onCopy(ok ? `${what} скопирована` : "Не удалось скопировать — выделите ссылку вручную", ok ? "ok" : "error"); };
  const items = (): MenuItem[] => [
    { id: "open", label: "Открыть встречу", icon: "arrowR", onSelect: () => navigate(m.url) },
    { id: "link", label: "Скопировать ссылку", icon: "copy", hidden: m.cancelled, onSelect: () => void copy(full, "Ссылка") },
    { id: "guest", label: "Скопировать гостевую ссылку", icon: "copy", hidden: !m.guest_token || m.cancelled, onSelect: () => void copy(`${window.location.origin}/guest/${m.guest_token}`, "Гостевая ссылка") },
    { id: "peer", label: "Управление ссылкой…", icon: "gear", hidden: m.kind !== "peer" || !m.can_manage || !onManagePeer, onSelect: () => onManagePeer?.(m.id) },
    { id: "tab", label: "Открыть в новой вкладке", icon: "arrowR", onSelect: () => { window.open(full, "_blank", "noopener"); } },
  ];
  const openFrom = (el: HTMLElement) => { const b = el.getBoundingClientRect(); openAt(Math.round(b.right), Math.round(b.bottom + 4), items()); };
  return { handler: onContextMenu(items), node, openFrom };
}

/** Кнопки быстрого копирования и «⋯» в одной строке в правом верхнем углу карточки (в строке списка — справа). Гостевая — по ответу сервера: ссылка есть либо её можно выпустить. */
function Actions({ m, menu, actions }: { m: UpcomingMeeting; menu: ReturnType<typeof useMeetingMenu>; actions: MeetingActions }) {
  const guest = m.cancelled ? null : m.guest_token ? { url: `${window.location.origin}/guest/${m.guest_token}` }
    : m.guest_issuable ? { url: null, issue: async () => { const r = await api.manage.guestLink(m.id, "issue"); if (!r.guest_token) throw new Error("Сервер не выпустил гостевую ссылку"); actions.onIssued?.(); return `${window.location.origin}/guest/${r.guest_token}`; } } : null;
  return (
    <span className="rc-actions inline">
      <LinkCopy name={m.name} staffUrl={m.cancelled ? null : window.location.origin + m.url} guest={guest} onCopy={actions.onCopy} />
      <button type="button" className="iconbtn rm-more" aria-label={`Действия: ${m.name}`} title="Действия (или правая кнопка мыши)" aria-haspopup="menu"
              onClick={(e) => { e.stopPropagation(); menu.openFrom(e.currentTarget); }}><Icon name="more" size={16} /></button>
    </span>
  );
}

const KIND_LABEL: Record<UpcomingMeeting["kind"], string> = { scheduled: "Из календаря", temporary: "Временная комната", peer: "Равноправная комната" };

/** Полный список приглашённых — по нажатию или наведению; загружается по требованию (права проверяет сервер). Окно — в document.body, чтобы не обрезалось карточкой. */
function People({ m }: { m: UpcomingMeeting }) {
  const { count, names, can_list } = m.invitees;
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number } | null>(null);
  const [list, setList] = useState<Invitee[] | null>(null);
  const [err, setErr] = useState("");
  const ref = useRef<HTMLButtonElement>(null);
  const hover = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!pos) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !(e.target instanceof Node && (ref.current?.contains(e.target) || document.getElementById(`upc-pop-${m.id}`)?.contains(e.target)))) setPos(null);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); window.removeEventListener("resize", close); window.removeEventListener("scroll", close, true); };
  }, [pos, m.id]);
  if (count === 0) return <span className="upc-people muted"><Icon name="users" size={14} /> Приглашённых нет</span>;
  const shown = names ?? [];
  const label = shown.length ? `${shown.join(", ")}${count > shown.length ? ` +${count - shown.length}` : ""}` : `${count} приглаш.`;
  if (!can_list) return <span className="upc-people" title="Список приглашённых видят организатор, руководители встречи и сами приглашённые"><Icon name="users" size={14} /> {count} приглаш.</span>;
  const open = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const w = Math.min(320, window.innerWidth - 16);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    // снизу места мало (карточка у края окна) — список раскрывается вверх
    setPos(window.innerHeight - r.bottom < 240 && r.top > window.innerHeight - r.bottom ? { left, bottom: window.innerHeight - r.top + 6 } : { left, top: r.bottom + 6 });
    if (list === null) api.invitees(m.id).then((x) => setList(x.items)).catch((e) => setErr((e as ApiError).message));
  };
  return (
    <>
      <button ref={ref} type="button" className="upc-people link" aria-expanded={!!pos} aria-haspopup="dialog" title={`Приглашено: ${count}`}
              onClick={(e) => { e.stopPropagation(); if (pos) setPos(null); else open(); }}
              onMouseEnter={() => { hover.current = window.setTimeout(open, 350); }} onMouseLeave={() => window.clearTimeout(hover.current)}>
        <Icon name="users" size={14} /> <span className="upc-people-names">{label}</span>
      </button>
      {pos && createPortal(
        <div id={`upc-pop-${m.id}`} className="upc-pop" role="dialog" aria-label={`Приглашённые: ${m.name}`} style={{ left: pos.left, top: pos.top, bottom: pos.bottom } as CSSProperties}>
          <div className="upc-pop-head">Приглашено: {count}</div>
          {err && <p className="small muted">{err}</p>}
          {!err && list === null && <p className="small muted">Загрузка…</p>}
          {list && (
            <ul className="upc-pop-list">
              {list.map((p, i) => (
                <li key={`${p.email ?? ""}-${i}`}>
                  <span className="upc-pop-name">{p.name ?? p.email ?? "Без имени"}{p.optional && <span className="tag">необязательно</span>}</span>
                  {p.name && p.email && <span className="small muted">{p.email}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>, document.body)}
    </>
  );
}

function SyncNote({ m }: { m: UpcomingMeeting }) {
  if (!m.sync || m.sync.state !== "error") return null;
  return (
    <span className="upc-sync" title="Календарь Outlook сейчас недоступен для сверки: показано последнее подтверждённое время и состав">
      <Icon name="retry" size={13} /> Сверено с календарём{m.sync.at ? ` в ${hhmm(m.sync.at)}` : " давно"}
    </span>
  );
}

function Go({ m, state, now, mini }: { m: UpcomingMeeting; state: ReturnType<typeof meetingState>["state"]; now: number; mini?: boolean }) {
  const a = action(m, state, now);
  if (!a) return null;
  return <Link to={m.url} className={`btn upc-go ${mini ? "mini" : ""} ${a.primary ? "primary" : ""}`} onClick={(e) => e.stopPropagation()}>{a.label}{a.primary && !mini && <Icon name="arrowR" size={15} />}</Link>;
}

export function MeetingCard({ m, now, i, actions }: { m: UpcomingMeeting; now: number; i: number; actions: MeetingActions }) {
  const st = meetingState(m, now);
  const menu = useMeetingMenu(m, actions);
  return (
    <article className={`upc-card upc-st-${st.state}`} style={{ "--i": i } as CSSProperties} aria-label={`${m.name}: ${STATE_LABEL[st.state]}`} onContextMenu={menu.handler}>
      {menu.node}
      <div className="upc-top">
        <span className={`upc-status upc-st-${st.state}`}><i aria-hidden />{STATE_LABEL[st.state]}</span>
        <Actions m={m} menu={menu} actions={actions} />
      </div>
      <h3 className="upc-title" title={m.name}>{m.name}</h3>
      <div className="upc-when"><Icon name="calendar" size={14} /> {whenLabel(m, now)}</div>
      <div className={`upc-count upc-st-${st.state}`} aria-live="off"><Icon name="clock" size={14} /> {st.text}</div>
      <div className="upc-meta">
        {m.organizer && <span className="upc-org" title="Организатор">Организатор: {m.organizer}</span>}
        <People m={m} />
      </div>
      <div className="upc-foot">
        <span className="upc-kind" title={m.kind === "scheduled" ? "Встреча запланирована в календаре Outlook" : "Комната на одну встречу"}>
          <Icon name={m.kind === "scheduled" ? "calendar" : "sparkle"} size={13} /> {KIND_LABEL[m.kind]}</span>
        <SyncNote m={m} />
        {m.has_password && <span className="tag" title="Для входа нужен пароль"><Icon name="lock" size={12} /> Пароль</span>}
        <span className="spacer" />
        <Go m={m} state={st.state} now={now} />
      </div>
    </article>
  );
}

export function MeetingRow({ m, now, actions }: { m: UpcomingMeeting; now: number; actions: MeetingActions }) {
  const st = meetingState(m, now);
  const menu = useMeetingMenu(m, actions);
  return (
    <div className={`upc-row upc-st-${st.state}`} role="listitem" onContextMenu={menu.handler}>
      {menu.node}
      <span className={`upc-status upc-st-${st.state}`}><i aria-hidden />{STATE_LABEL[st.state]}</span>
      <span className="upc-row-main">
        <span className="upc-row-name" title={m.name}>{m.name}</span>
        <span className="small muted">{whenLabel(m, now)} · {st.text}</span>
      </span>
      <People m={m} />
      <span className="upc-row-act"><Actions m={m} menu={menu} actions={actions} /><Go m={m} state={st.state} now={now} mini /></span>
    </div>
  );
}

const FOLD = 6;           // сколько встреч видно сразу; остальные — «Показать все»

/** Блок «Предстоящие встречи» над постоянными переговорками. Пустой — не показывается вовсе (решает родитель). */
export default function UpcomingSection({ items, now, view, mineOnly, onMineOnly, othersCount, actions }: {
  items: UpcomingMeeting[]; now: number; view: "tiles" | "list"; mineOnly: boolean | null; onMineOnly: (v: boolean) => void; othersCount: number; actions: MeetingActions;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, FOLD);
  return (
    <section className="upc-section" aria-labelledby="upc-h">
      <div className="upc-head">
        <h2 id="upc-h">Предстоящие встречи <span className="muted">{items.length}</span></h2>
        {mineOnly !== null && (
          <label className="check small" title="Администратор видит все встречи организации; по умолчанию — только свои">
            <input type="checkbox" checked={!mineOnly} onChange={(e) => onMineOnly(!e.target.checked)} /> Все встречи организации{othersCount ? ` (+${othersCount})` : ""}
          </label>
        )}
      </div>
      {items.length === 0 && <p className="muted small">Ваших встреч нет. Встречи организации — по переключателю выше.</p>}
      {view === "tiles"
        ? <div className="upc-grid">{shown.map((m, i) => <MeetingCard key={m.id} m={m} now={now} i={i} actions={actions} />)}</div>
        : <div className="upc-list" role="list">{shown.map((m) => <MeetingRow key={m.id} m={m} now={now} actions={actions} />)}</div>}
      {items.length > FOLD && (
        <button type="button" className="btn ghost sm upc-more" onClick={() => setAll((v) => !v)} aria-expanded={all}>
          {all ? "Свернуть" : `Показать все (${items.length})`}
        </button>
      )}
    </section>
  );
}
