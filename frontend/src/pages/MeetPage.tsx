import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type OutlookPublic } from "../api";
import { Brand } from "../components/Brand";

/** Разбор остатка до начала для обратного отсчёта: «2 д 03:15:09» / «03:15:09» / «00:42». */
export function countdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d ? `${d} д ` : ""}${h || d ? `${p(h)}:` : ""}${p(m)}:${p(sec)}`;
}

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU", { dateStyle: "full", timeStyle: "short" }) : "");
const fmtShort = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" }) : "");

/** Страница конференции из календаря (`/m/<код>`): без входа показывает название, время и обратный отсчёт; когда вход открыт — ведёт в комнату (или предлагает войти / войти гостем). */
export default function MeetPage() {
  const { code = "" } = useParams();
  const navigate = useNavigate();
  const [info, setInfo] = useState<OutlookPublic | null>(null);
  const [err, setErr] = useState("");
  const [now, setNow] = useState(Date.now());
  const skew = useRef(0);
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await api.outlookPublic(code);
        if (!alive) return;
        skew.current = new Date(r.server_time).getTime() - Date.now();
        setInfo(r); setErr("");
      } catch (e) { if (alive) setErr((e as ApiError).status === 404 ? "Конференция не найдена. Ссылка могла устареть, а встречу — отменить." : (e as ApiError).message); }
    };
    void load();
    const t = window.setInterval(() => void load(), 15000);
    return () => { alive = false; window.clearInterval(t); };
  }, [code]);
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  useEffect(() => { void api.me().then(() => setAuthed(true)).catch(() => setAuthed(false)); }, []);

  // момент открытия наступил по часам сервера — перезапрашиваем состояние, не дожидаясь следующего опроса
  const left = info?.opens_at ? new Date(info.opens_at).getTime() - (now + skew.current) : 0;
  useEffect(() => {
    if (info?.state === "waiting" && left <= 0) void api.outlookPublic(code).then(setInfo).catch(() => undefined);
  }, [info?.state, left <= 0, code]);                                   // eslint-disable-line react-hooks/exhaustive-deps
  const open = info?.state === "open" || info?.state === "live";
  useEffect(() => { if (open && authed && info) navigate(`/rooms/${info.slug}`, { replace: true }); }, [open, authed, info, navigate]);

  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <div className="shell guest-shell">
      <header className="topbar"><span className="brand"><Brand /></span></header>
      <main>
        <section className="prejoin card" aria-live="polite">
          {err && <><h1>Конференция недоступна</h1><p className="muted">{err}</p><p><Link className="btn" to="/">На главную</Link></p></>}
          {!err && !info && <p className="muted">Загрузка…</p>}
          {info && (
            <>
              <h1>{info.name}</h1>
              {info.recurring ? <p className="muted">Регулярная встреча: вход открыт всегда, начало — по расписанию в вашем календаре.</p>
                : <p className="muted">{fmt(info.start)} — {info.end ? new Date(info.end).toLocaleTimeString("ru-RU", { timeStyle: "short" }) : ""} <span className="small">({tz})</span></p>}
              {info.state === "waiting" && (
                <div className="alert" role="status">
                  <p style={{ margin: 0 }}>Подключиться можно за {info.early_minutes} мин до начала — с {fmtShort(info.opens_at)}.</p>
                  <p style={{ fontSize: 34, margin: "8px 0 0", fontVariantNumeric: "tabular-nums" }} aria-label="Время до открытия">{countdown(left)}</p>
                </div>
              )}
              {info.state === "cancelled" && <div className="alert error" role="status">Встреча отменена организатором.</div>}
              {info.state === "ended" && <div className="alert" role="status">Встреча завершена. Материалы доступны участникам в «Истории».</div>}
              {open && authed === false && (
                <div className="row">
                  <Link className="btn primary" to={`/?next=${encodeURIComponent(`/m/${code}`)}`}>Войти по учётной записи</Link>
                  {info.guests && info.guest_token && <Link className="btn" to={`/guest/${info.guest_token}`}>Войти гостем</Link>}
                </div>
              )}
              {open && authed && <p className="muted">Открываем комнату…</p>}
            </>
          )}
        </section>
      </main>
    </div>
  );
}
