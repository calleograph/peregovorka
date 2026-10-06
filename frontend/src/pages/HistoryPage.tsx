import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ExportFormat, type Meeting } from "../api";
import Menu from "../components/Menu";
import MeetingAdminActions from "../components/MeetingAdminActions";
import { duration, fmt } from "../util";

export { fmt } from "../util";
const FORMATS: [ExportFormat, string][] = [["docx", "Word (.docx)"], ["pdf", "PDF (.pdf)"], ["md", "Markdown (.md)"], ["txt", "Обычный текст (.txt)"]];

export default function HistoryPage({ isAdmin = false }: { isAdmin?: boolean }) {
  const [items, setItems] = useState<Meeting[] | null>(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const load = useCallback(() => api.meetings().then(setItems).catch((e) => setError(e.message)), []);
  useEffect(() => { void load(); }, [load]);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (items ?? []).filter((m) => !s || m.room_name.toLowerCase().includes(s) || m.participants.some((p) => p.display_name.toLowerCase().includes(s)));
  }, [items, q]);

  if (error) return <div className="alert error">{error}</div>;
  if (!items) return <div className="muted">Загрузка…</div>;
  return (
    <section>
      <div className="row"><h1 style={{ margin: 0 }}>История встреч</h1><div className="spacer" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск: комната или участник" style={{ maxWidth: 320 }} aria-label="Поиск по истории" /></div>
      {isAdmin
        ? <p className="muted">Как администратор вы видите все встречи.</p>
        : <p className="muted">Здесь — встречи, к которым у вас есть доступ: завершённая встреча доступна, пока открыта её страница, а также если так настроено для комнаты или выдано администратором.</p>}
      {shown.length === 0 && <p className="muted">{items.length ? "Ничего не найдено." : "Пока нет доступных встреч."}</p>}
      <table className="table">
        <thead><tr><th>Комната</th><th>Начало</th><th>Длительность</th><th>Участники</th><th>Материалы</th><th /></tr></thead>
        <tbody>
          {shown.map((m) => (
            <tr key={m.id}>
              <td><Link to={`/history/${m.id}`}>{m.room_name}</Link></td>
              <td>{fmt(m.started_at)}</td>
              <td>{m.ended_at ? duration(m.started_at, m.ended_at) : <span className="badge rec">идёт</span>}</td>
              <td>{m.participants.map((p) => p.display_name).join(", ")}</td>
              <td className="small muted">реплик: {m.segments} · документов: {m.protocols}{isAdmin ? ` · записей: ${m.recordings}` : ""}</td>
              <td className="actions">
                <Link className="btn mini" to={`/history/${m.id}`}>Открыть</Link>{" "}
                <Menu label="Скачать" className="btn mini" title="Стенограмма встречи">
                  {FORMATS.map(([f, l]) => <a key={f} href={api.transcriptExportUrl(m.id, f)} download>Стенограмма: {l}</a>)}
                </Menu>{" "}
                {isAdmin && <MeetingAdminActions meeting={m} onChanged={load} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
