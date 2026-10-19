import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type RoomJournal as Data } from "../api";
import { fmt } from "../util";

const RESULT: Record<string, string> = { success: "ок", failure: "ошибка", pending: "в очереди" };
const DETAIL: Record<string, string> = { user: "кому", via: "способ", group: "группа", members: "человек", added: "добавлено", rights: "права", by: "кем", kinds: "материалы", count: "число", recipient: "получатель", trigger: "причина", error: "ошибка" };
const text = (v: unknown) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));

/** «История действий» комнаты: вход/выход, встречи, доступ, материалы, рассылка. Время хранится в UTC, показывается в часовом поясе браузера. Паролей, токенов и содержимого материалов здесь нет. */
export default function RoomJournal({ roomId, meetingId }: { roomId: string; meetingId?: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [f, setF] = useState({ from: "", to: "", kind: "", user: "", meeting: meetingId ?? "" });
  const [page, setPage] = useState(0);
  const PAGE = 50;
  const load = useCallback(() => {
    const p: Record<string, string> = { limit: String(PAGE), offset: String(page * PAGE) };
    if (f.from) p.from = new Date(f.from).toISOString();
    if (f.to) p.to = new Date(f.to).toISOString();
    if (f.kind) p.kind = f.kind;
    if (f.user.trim()) p.user = f.user.trim();
    if (f.meeting) p.meeting_id = f.meeting;
    setErr("");
    api.roomJournal(roomId, p).then(setData).catch((e) => setErr((e as ApiError).message));
  }, [roomId, f, page]);
  useEffect(load, [load]);
  const kinds = data ? (Array.isArray(data.kinds) ? Object.fromEntries(data.kinds.map((k) => [k, k])) : data.kinds) : {};
  const set = (patch: Partial<typeof f>) => { setPage(0); setF({ ...f, ...patch }); };
  return (
    <div className="card">
      <div className="row" style={{ flexWrap: "wrap" }}>
        <label>С <input type="datetime-local" value={f.from} onChange={(e) => set({ from: e.target.value })} /></label>
        <label>По <input type="datetime-local" value={f.to} onChange={(e) => set({ to: e.target.value })} /></label>
        <label>Тип <select value={f.kind} onChange={(e) => set({ kind: e.target.value })}><option value="">Все</option>{Object.entries(kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        {!meetingId && data && <label>Встреча <select value={f.meeting} onChange={(e) => set({ meeting: e.target.value })}><option value="">Все</option>{data.meetings.map((m) => <option key={m.id} value={m.id}>{fmt(m.started_at)}</option>)}</select></label>}
        <label>Пользователь <input value={f.user} onChange={(e) => set({ user: e.target.value })} placeholder="имя или логин" /></label>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {data && data.items.length === 0 && <p className="muted">Событий по этим условиям нет.</p>}
      {data && data.items.length > 0 && (
        <table className="table"><thead><tr><th>Время</th><th>Событие</th><th>Кто</th><th>Подробности</th><th /></tr></thead><tbody>
          {data.items.map((e, i) => (
            <tr key={`${e.at}-${e.action}-${i}`}><td title={e.at}>{fmt(e.at)}</td><td>{e.title}</td><td>{e.actor ?? "—"}</td>
              <td className="small">{Object.entries(e.details).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${DETAIL[k] ?? k}: ${text(v)}`).join("; ")}</td>
              <td>{e.result && <span className={`badge ${e.result === "failure" ? "warn" : ""}`}>{RESULT[e.result] ?? e.result}</span>}</td></tr>))}
        </tbody></table>)}
      {data && <div className="row"><button className="btn mini" disabled={page === 0} onClick={() => setPage(page - 1)}>← Новее</button>
        <span className="muted small">{data.total} событий</span>
        <button className="btn mini" disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(page + 1)}>Старее →</button></div>}
    </div>
  );
}
