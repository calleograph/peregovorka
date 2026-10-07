import { Suspense, lazy, useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, setCsrf, setUnauthorizedHandler, type Me } from "./api";
import { useActiveMeeting } from "./activeMeeting";
import GuestPage from "./pages/GuestPage";
import HistoryPage from "./pages/HistoryPage";
import LoginPage from "./pages/LoginPage";
import MeetingPage from "./pages/MeetingPage";
import RoomsPage from "./pages/RoomsPage";

// Тяжёлые части (livekit-client, админка) грузятся только когда нужны.
const RoomPage = lazy(() => import("./pages/RoomPage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));

/** Раздел верхней панели. Пока в этой вкладке идёт встреча, раздел открывается в НОВОЙ вкладке: уход со страницы комнаты оборвал бы звонок. */
function NavItem({ to, end, newTab, children }: { to: string; end?: boolean; newTab: boolean; children: ReactNode }) {
  if (newTab) return <a href={to} target="_blank" rel="noopener" className="nav-newtab" title="Откроется в новой вкладке — текущая встреча не прервётся">{children}<span aria-hidden> ↗</span></a>;
  return <NavLink to={to} end={end}>{children}</NavLink>;
}

function StaffApp() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined — ещё проверяем сессию
  const [version, setVersion] = useState("");
  const navigate = useNavigate();
  const inMeeting = useActiveMeeting() !== null;

  useEffect(() => {
    api.me().then((m) => { setCsrf(m.csrf_token); setMe(m); }).catch(() => setMe(null));
    api.version().then((v) => setVersion(`${v.version} · ${v.commit.slice(0, 8)}`)).catch(() => undefined);
  }, []);

  useEffect(() => { setUnauthorizedHandler(() => { setCsrf(""); setMe(null); }); return () => setUnauthorizedHandler(null); }, []);

  const onLogin = useCallback((m: Me) => { setCsrf(m.csrf_token); setMe(m); navigate("/"); }, [navigate]);
  const logout = async () => { await api.logout().catch(() => undefined); setCsrf(""); setMe(null); navigate("/"); };

  if (me === undefined) return <div className="center muted">Загрузка…</div>;
  if (me === null) return <LoginPage onLogin={onLogin} version={version} />;

  return (
    <div className="shell">
      <header className="topbar">
        {inMeeting
          ? <a href="/" target="_blank" rel="noopener" className="brand" title="Откроется в новой вкладке">Переговорка ↗</a>
          : <Link to="/" className="brand">Переговорка</Link>}
        <nav>
          <NavItem to="/" end newTab={inMeeting}>Комнаты</NavItem>
          <NavItem to="/history" newTab={inMeeting}>История</NavItem>
          {me.user.is_admin && <NavItem to="/admin" newTab={inMeeting}>Администрирование</NavItem>}
        </nav>
        <div className="spacer" />
        <span className="muted">{me.user.display_name}</span>
        <button className="btn ghost" onClick={logout} disabled={inMeeting} title={inMeeting ? "Сначала выйдите из комнаты: выход из системы прервёт встречу" : undefined}>Выйти</button>
      </header>
      <main>
        <Suspense fallback={<div className="muted">Загрузка…</div>}>
        <Routes>
          <Route path="/" element={<RoomsPage />} />
          <Route path="/rooms/:roomId" element={<RoomPage selfName={me.user.display_name} />} />
          <Route path="/history" element={<HistoryPage isAdmin={me.user.is_admin} />} />
          <Route path="/history/:meetingId" element={<MeetingPage isAdmin={me.user.is_admin} />} />
          <Route path="/admin" element={me.user.is_admin ? <AdminPage version={version} /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}

/** Гостевая оболочка: без входа в систему, без разделов и истории — только страница гостя. */
function GuestApp() {
  return (
    <div className="shell guest-shell">
      <header className="topbar"><span className="brand">Переговорка</span><span className="muted">Гостевой доступ</span></header>
      <main>
        <Routes>
          <Route path="/guest/:token" element={<GuestPage />} />
          <Route path="*" element={<section className="prejoin card"><h1>Страница не найдена</h1><p className="muted">Проверьте гостевую ссылку.</p></section>} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  const { pathname } = useLocation();
  return pathname.startsWith("/guest/") ? <GuestApp /> : <StaffApp />;
}
