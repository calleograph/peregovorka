import { type CSSProperties, FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError, type Room, type TempRoomPolicy } from "../api";
import { Icon } from "../components/Icons";
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

function RoomCard({ r, i }: { r: Room; i: number }) {
  const t = tint(r.name);
  const live = r.active_meeting;
  const full = live && live.participants >= r.max_participants;
  return (
    <Link to={`/rooms/${r.slug}`} className={`room-card rc ${live ? "is-live" : ""}`} style={{ "--i": i, "--ha": t.a, "--hb": t.b } as CSSProperties} onPointerMove={spotlight}
          aria-label={`${r.name}: ${live ? `идёт встреча, участников ${live.participants}` : "свободна"}`}>
      <span className="rc-glow" aria-hidden />
      <div className="rc-top">
        <span className="rc-mark" aria-hidden>{initials(r.name)}</span>
        <div className="rc-title">
          <h2>{r.name}</h2>
          <span className={`pill ${live ? "live" : "free"}`}>
            <i className="pulse" aria-hidden />{live ? `Идёт встреча · ${live.participants} уч.` : "Свободна"}
          </span>
        </div>
        {r.has_password && <span className="rc-lock" title="Для входа нужен пароль"><Icon name="lock" size={16} /></span>}
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
  );
}

export default function RoomsPage() {
  const [rooms, setRooms] = useState<Room[] | null>(null);
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<TempRoomPolicy | null>(null);
  const [tempOpen, setTempOpen] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

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
          <p className="eyebrow">Peregovorka</p>
          <h1>Переговорки</h1>
          <p className="lead">
            {rooms.length === 0 ? "Для вас пока нет доступных комнат." : liveCount > 0 ? `Сейчас идут встречи: ${liveCount}. Выберите комнату, чтобы присоединиться или начать новую.` : "Выберите комнату, чтобы начать встречу. Речь будет записана в стенограмму и станет протоколом."}
          </p>
        </div>
        {canTemp && (
          <button className="btn primary cta" {...magnet} onClick={() => setTempOpen(true)} disabled={!policy?.can_create}
                  title={policy?.can_create ? "Комната на одну встречу: закрывается сама, материалы остаются в «Истории»" : `Уже ${policy?.active_mine} активных временных переговорок (предел ${policy?.max_per_user})`}>
            <Icon name="sparkle" size={17} /> Временная переговорка
          </button>
        )}
      </header>

      {rooms.length > 0 && (
        <div className="rooms-toolbar">
          <label className="search"><Icon name="search" size={16} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти комнату" aria-label="Найти комнату" /></label>
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
      <div className="grid rooms-grid">{shown.map((r, i) => <RoomCard key={r.id} r={r} i={i} />)}</div>
      {tempOpen && policy && <TempRoomDialog policy={policy} onClose={() => setTempOpen(false)} />}
    </section>
  );
}
