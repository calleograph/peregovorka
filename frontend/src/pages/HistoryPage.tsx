import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Meeting } from "../api";

export const fmt = (iso: string) => new Date(iso).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });

export default function HistoryPage() {
  const [items, setItems] = useState<Meeting[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.meetings().then(setItems).catch((e) => setError(e.message)); }, []);

  if (error) return <div className="alert error">{error}</div>;
  if (!items) return <div className="muted">Загрузка…</div>;
  return (
    <section>
      <h1>История встреч</h1>
      {items.length === 0 && <p className="muted">Пока нет встреч, в которых вы участвовали.</p>}
      <table className="table">
        <thead><tr><th>Комната</th><th>Начало</th><th>Окончание</th><th>Участники</th><th /></tr></thead>
        <tbody>
          {items.map((m) => (
            <tr key={m.id}>
              <td>{m.room_name}</td>
              <td>{fmt(m.started_at)}</td>
              <td>{m.ended_at ? fmt(m.ended_at) : <span className="badge">идёт</span>}</td>
              <td>{m.participants.map((p) => p.display_name).join(", ")}</td>
              <td><Link to={`/history/${m.id}`}>Открыть протокол</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
