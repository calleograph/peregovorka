import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/Icons";
import { api, type ExportFormat, type Meeting } from "../api";
import Menu from "../components/Menu";
import MeetingAdminActions from "../components/MeetingAdminActions";
import { ListFooter, useInfinite } from "../useInfinite";
import { duration, fmt } from "../util";

export { fmt } from "../util";
const FORMATS: [ExportFormat, string][] = [["docx", "Word (.docx)"], ["pdf", "PDF (.pdf)"], ["md", "Markdown (.md)"], ["txt", "Обычный текст (.txt)"]];

/** Материалы встречи: значки со счётчиками вместо длинной строки; полное название — в подсказке. */
function Materials({ m, isAdmin }: { m: Meeting; isAdmin: boolean }) {
  const items: [Parameters<typeof Icon>[0]["name"], number, string][] = [
    ["transcript", m.segments, "реплик стенограммы"], ["file", m.protocols, "документов (протоколов и резюме)"],
    ...(isAdmin ? [["record", m.recordings, "записей аудио"] as [Parameters<typeof Icon>[0]["name"], number, string]] : []),
    ["chat", m.chat_messages ?? 0, "сообщений чата"],
  ];
  const shown = items.filter(([, n]) => n > 0);
  if (!shown.length && !m.whiteboard_shapes) return <span className="muted small">—</span>;
  return (
    <span className="mat" aria-label={shown.map(([, n, t]) => `${n} ${t}`).join(", ")}>
      {shown.map(([icon, n, t]) => <span key={t} className="mat-i" title={`${n} ${t}`}><Icon name={icon} size={14} />{n}</span>)}
      {m.whiteboard_shapes ? <span className="mat-i" title="есть схема на доске"><Icon name="board" size={14} /></span> : null}
    </span>
  );
}

export default function HistoryPage({ isAdmin = false }: { isAdmin?: boolean }) {
  const [q, setQ] = useState("");
  // «бесконечная лента»: следующие встречи подгружаются при прокрутке; поиск работает по уже загруженным (лента догружается, пока список виден)
  const list = useInfinite<Meeting>(async (offset) => { const r = await api.meetings(undefined, offset); return { rows: r, more: r.length >= 30 }; }, []);
  const items = list.items;
  const load = list.reload;

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return items.filter((m) => !s || m.room_name.toLowerCase().includes(s) || m.participants.some((p) => p.display_name.toLowerCase().includes(s)));
  }, [items, q]);

  return (
    <section>
      <div className="row"><h1 style={{ margin: 0 }}>История встреч</h1><div className="spacer" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск: комната или участник" style={{ maxWidth: 320 }} aria-label="Поиск по истории" /></div>
      {isAdmin
        ? <p className="muted">Как администратор вы видите все встречи.</p>
        : <p className="muted">Здесь — встречи, к которым у вас есть доступ: завершённая встреча доступна, пока открыта её страница, а также если так настроено для комнаты или выдано администратором.</p>}
      {list.error && <div className="alert error">{list.error}</div>}
      {shown.length === 0 && list.done && <p className="muted">{items.length ? "Ничего не найдено." : "Пока нет доступных встреч."}</p>}
      <div className="table-scroll"><table className="table hist-table">
        <thead><tr><th>Комната</th><th>Начало</th><th>Длительность</th><th>Участники</th><th>Материалы</th><th /></tr></thead>
        <tbody>
          {shown.map((m) => (
            <tr key={m.id}>
              <td className="h-room"><Link to={`/history/${m.id}`}>{m.room_name}</Link></td>
              <td data-label="Начало">{fmt(m.started_at)}</td>
              <td data-label="Длительность">{m.ended_at ? duration(m.started_at, m.ended_at) : <span className="badge rec">идёт</span>}</td>
              <td data-label="Участники" className="h-people" title={m.participants.map((p) => p.display_name).join(", ")}>{m.participants.map((p) => p.display_name).join(", ")}</td>
              <td data-label="Материалы"><Materials m={m} isAdmin={isAdmin} /></td>
              <td className="actions">
                <Link className="btn mini primary" to={`/history/${m.id}`}>Открыть</Link>{" "}
                <Menu label="Ещё" className="btn mini" title="Скачать стенограмму">
                  {FORMATS.map(([f, l]) => <a key={f} href={api.transcriptExportUrl(m.id, f)} download>Стенограмма: {l}</a>)}
                </Menu>{" "}
                {isAdmin && <MeetingAdminActions meeting={m} onChanged={load} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <ListFooter loading={list.loading} done={list.done} error="" sentinel={list.sentinel} count={items.length} empty="" />
    </section>
  );
}
