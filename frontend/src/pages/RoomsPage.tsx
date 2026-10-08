import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Room } from "../api";

export default function RoomsPage() {
  const [rooms, setRooms] = useState<Room[] | null>(null);
  const [error, setError] = useState("");

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
      <h1>Доступные переговорки</h1>
      {rooms.length === 0 && <p className="muted">Для вас нет доступных комнат. Обратитесь к администратору.</p>}
      <div className="grid">
        {rooms.map((r) => (
          <Link key={r.id} to={`/rooms/${r.id}`} className="card room-card">
            <div className="row">
              <h2>{r.name}</h2>
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
    </section>
  );
}
