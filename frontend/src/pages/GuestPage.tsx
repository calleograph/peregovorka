import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useSite } from "../site";
import { api, ApiError, setGuestToken, setUnauthorizedHandler, type GuestJoinInfo, type GuestRoomInfo } from "../api";
import PreJoinCheck from "../components/PreJoinCheck";
import { setPreJoin, type PreJoin } from "../prejoin";
import type { GuestSession } from "./RoomPage";

const RoomPage = lazy(() => import("./RoomPage"));
const NAME_KEY = "guest.name";
const ssGet = (k: string) => { try { return sessionStorage.getItem(k) ?? ""; } catch { return ""; } };
const ssSet = (k: string, v: string) => { try { sessionStorage.setItem(k, v); } catch { /* не критично */ } };

type Phase = "check" | "room" | "left" | "ended" | "revoked";

/**
 * Вход гостя по ссылке без AD: имя → проверка оборудования → «Войти в комнату». Гость — отдельный тип участника: в списке он виден как
 * «Имя (гость)», административных функций, истории, стенограммы и показа экрана у него нет. Сессия живёт только в этой вкладке.
 */
export default function GuestPage() {
  const { token = "" } = useParams();
  const [info, setInfo] = useState<GuestRoomInfo | null>(null);
  const [fatal, setFatal] = useState("");
  const [name, setName] = useState(() => ssGet(NAME_KEY));
  const [password, setPassword] = useState("");
  const [needPw, setNeedPw] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<Phase>("check");
  const [session, setSession] = useState<GuestJoinInfo | null>(null);
  const hw = useRef<PreJoin & { micOk: boolean }>({ camOn: false, micOk: false });
  const site = useSite();
  const consentDocs = site.documents.filter((d) => d.require_consent);       // документы организации, которые гость должен явно подтвердить
  const [agreed, setAgreed] = useState<Set<string>>(() => new Set());
  const consentOk = consentDocs.every((d) => agreed.has(d.kind));

  const loadInfo = useCallback(() => api.guest.room(token).then((r) => { setInfo(r); setFatal(""); })
    .catch((e) => { if ((e as ApiError).status === 404) setFatal("Гостевая ссылка недействительна или отозвана. Попросите организатора прислать новую."); else setError((e as ApiError).message); }), [token]);
  useEffect(() => { void loadInfo(); }, [loadInfo]);
  // пока встреча не началась — ждём; страница сама подхватит начало
  useEffect(() => {
    if (phase !== "check" || fatal || (info && info.meeting_active)) return;
    const t = window.setInterval(() => void loadInfo(), 5000);
    return () => window.clearInterval(t);
  }, [phase, fatal, info, loadInfo]);

  useEffect(() => {
    setUnauthorizedHandler(() => { setGuestToken(""); setPhase("revoked"); });
    return () => setUnauthorizedHandler(null);
  }, []);

  const onHw = useCallback((p: PreJoin & { micOk: boolean }) => { hw.current = p; }, []);

  const enter = async () => {
    setBusy(true); setError("");
    try {
      const r = await api.guest.join(token, name.trim(), needPw ? password : undefined, consentDocs.map((d) => d.kind).filter((k) => agreed.has(k)));
      ssSet(NAME_KEY, name.trim());
      setGuestToken(r.guest_token);
      const { micOk: _ok, ...pre } = hw.current; void _ok;
      setPreJoin(pre);
      setSession(r);
      setPhase("room");
    } catch (e) {
      const ae = e as ApiError;
      if (ae.code === "room_password_required" || ae.code === "room_password_invalid") { setNeedPw(true); setError(ae.code === "room_password_invalid" ? ae.message : ""); }
      else if (ae.status === 404) setFatal("Гостевая ссылка недействительна или отозвана.");
      else setError(ae.message || "Не удалось войти");
      void loadInfo();
    }
    setBusy(false);
  };

  const guestCtx = useMemo<GuestSession | undefined>(() => session ? {
    info: session,
    onLeft: (why) => { setGuestToken(""); setSession(null); setPhase(why === "ended" ? "ended" : "left"); },
  } : undefined, [session]);

  if (fatal) return <section className="prejoin card"><h1>Ссылка недоступна</h1><div className="alert error" role="alert">{fatal}</div></section>;
  if (phase === "revoked") return <section className="prejoin card"><h1>Доступ закрыт</h1><div className="alert error" role="alert">Гостевая ссылка отозвана или сессия истекла. Вы отключены от встречи.</div></section>;
  if (phase === "ended") return <section className="prejoin card"><h1>Встреча завершена</h1><p className="muted">Спасибо за участие. Эту вкладку можно закрыть.</p></section>;
  if (phase === "left") return (
    <section className="prejoin card"><h1>Вы вышли из встречи</h1><p className="muted">Встреча продолжается у остальных участников.</p>
      <div className="row"><button className="btn primary" onClick={() => { setPhase("check"); void loadInfo(); }}>Войти снова</button></div></section>
  );
  if (phase === "room" && guestCtx) return <Suspense fallback={<div className="center muted">Загрузка…</div>}><RoomPage guest={guestCtx} /></Suspense>;

  if (!info) return <div className="center muted">Загрузка…</div>;
  const nameOk = name.trim().length >= 2;
  return (
    <section className="prejoin card guest-join">
      <h1>{info.room_name}</h1>
      <p className="muted">Вы входите как гость. Укажите, как вас называть, и проверьте оборудование.</p>
      {info.description && <p className="small">{info.description}</p>}
      {site.guest_text && <div className="alert info" role="note" style={{ whiteSpace: "pre-wrap" }}>{site.guest_text}</div>}
      {!info.meeting_active && <div className="alert info" role="status">Встреча ещё не началась. Дождитесь, пока сотрудник откроет комнату — страница обновится сама.</div>}
      <label>Ваше имя
        <input value={name} maxLength={60} autoFocus autoComplete="name" onChange={(e) => setName(e.target.value)} placeholder="Например, Иван Иванов"
               onKeyDown={(e) => { if (e.key === "Enter" && nameOk && info.meeting_active && !busy) void enter(); }} />
      </label>
      {name.trim() && <p className="muted small">Участники увидят: <b>{name.trim().replace(/\s+/g, " ")} (гость)</b></p>}
      {(info.has_password || needPw) && (
        <label>Пароль комнаты
          <input type="password" value={password} onChange={(e) => { setPassword(e.target.value); setNeedPw(true); }} autoComplete="off" />
        </label>
      )}
      <PreJoinCheck cameraAllowed={info.camera_allowed} onChange={onHw} />
      {consentDocs.map((d) => (
        <label key={d.kind} className="check"><input type="checkbox" checked={agreed.has(d.kind)} onChange={(e) => setAgreed((s) => { const n = new Set(s); if (e.target.checked) n.add(d.kind); else n.delete(d.kind); return n; })} />
          <span className="check-body"><span>Я ознакомился(лась) с документом «<Link to={`/legal/${d.kind}`} target="_blank" rel="noopener noreferrer">{d.title}</Link>»</span></span></label>
      ))}
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="row">
        <button className="btn primary" disabled={busy || !nameOk || !consentOk || !info.meeting_active || ((info.has_password || needPw) && !password)} onClick={() => void enter()}>
          {busy ? "Входим…" : "Войти в комнату"}
        </button>
      </div>
      {site.recording_text && <p className="small" style={{ whiteSpace: "pre-wrap" }}>{site.recording_text}</p>}
      <p className="muted small">Запись и транскрибация встречи могут вестись. Чат и общая доска доступны внутри комнаты.</p>
    </section>
  );
}
