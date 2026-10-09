import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ApiError, type Room, type TempRoomPolicy } from "../api";
import { Modal } from "../components/Dialogs";

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

export default function RoomsPage() {
  const [rooms, setRooms] = useState<Room[] | null>(null);
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<TempRoomPolicy | null>(null);
  const [tempOpen, setTempOpen] = useState(false);

  useEffect(() => { void api.temporaryPolicy().then(setPolicy).catch(() => undefined); }, [tempOpen]);
  useEffect(() => {
    const load = () => api.rooms().then(setRooms).catch((e) => setError(e.message));
    load();
    const t = window.setInterval(load, 10000); // «кто сейчас в комнате» обновляется без перезагрузки
    return () => window.clearInterval(t);
  }, []);

  if (error) return <div className="alert error">{error}</div>;
  if (!rooms) return <div className="muted">Загрузка…</div>;

  return (
    <section>
      <div className="row">
        <h1 style={{ margin: 0 }}>Доступные переговорки</h1><div className="spacer" />
        {policy?.enabled && (
          <button className="btn" onClick={() => setTempOpen(true)} disabled={!policy.can_create}
                  title={policy.can_create ? "Комната на одну встречу: закрывается сама, материалы остаются в «Истории»" : `Уже ${policy.active_mine} активных временных переговорок (предел ${policy.max_per_user})`}>
            ＋ Создать временную переговорку
          </button>
        )}
      </div>
      {rooms.length === 0 && <p className="muted">Для вас нет доступных комнат. Обратитесь к администратору.</p>}
      <div className="grid">
        {rooms.map((r) => (
          <Link key={r.id} to={`/rooms/${r.slug}`} className="card room-card">
            <div className="row">
              <h2>{r.name}</h2>
              {r.lifetime === "temporary" && <span className="badge" title={r.lifecycle === "grace_period" ? "Все вышли: комната закроется, если никто не вернётся" : "Временная переговорка"}>{r.lifecycle === "grace_period" ? "Временная · ждёт возврата" : "Временная"}</span>}
              {r.has_password && <span className="badge" title="Нужен пароль">🔒</span>}
            </div>
            {r.description && <p className="muted">{r.description}</p>}
            <div className="row small muted">
              <span>{r.active_meeting ? `Идёт встреча · ${r.active_meeting.participants} уч.` : "Свободна"}</span>
              <span>макс. {r.max_participants}</span>
            </div>
            <div className="row small">
              {r.room_type === "presentation" && <span className="badge" title="Участники слушают; говорят руководители и те, кому дали слово">Презентация</span>}
              {r.transcription_enabled && <span className="badge">Транскрибация</span>}
              {r.auto_record ? <span className="badge warn" title="Запись звука начинается вместе со встречей">Запись автоматически</span>
                : r.record_audio && <span className="badge warn" title="Руководитель может включить запись звука">Запись аудио</span>}
            </div>
          </Link>
        ))}
      </div>
      {tempOpen && policy && <TempRoomDialog policy={policy} onClose={() => setTempOpen(false)} />}
    </section>
  );
}
