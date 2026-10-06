import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, setCsrf, setUnauthorizedHandler, type Me } from "./api";
import HistoryPage from "./pages/HistoryPage";
import LoginPage from "./pages/LoginPage";
import MeetingPage from "./pages/MeetingPage";
import RoomsPage from "./pages/RoomsPage";

// Тяжёлые части (livekit-client, админка) грузятся только когда нужны.
const RoomPage = lazy(() => import("./pages/RoomPage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined — ещё проверяем сессию
  const [version, setVersion] = useState("");
  const navigate = useNavigate();

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
        <Link to="/" className="brand">Переговорка</Link>
        <nav>
          <NavLink to="/" end>Комнаты</NavLink>
          <NavLink to="/history">История</NavLink>
          {me.user.is_admin && <NavLink to="/admin">Администрирование</NavLink>}
        </nav>
        <div className="spacer" />
        <span className="muted">{me.user.display_name}</span>
        <button className="btn ghost" onClick={logout}>Выйти</button>
      </header>
      <main>
        <Suspense fallback={<div className="muted">Загрузка…</div>}>
        <Routes>
          <Route path="/" element={<RoomsPage />} />
          <Route path="/rooms/:roomId" element={<RoomPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/history/:meetingId" element={<MeetingPage />} />
          <Route path="/admin" element={me.user.is_admin ? <AdminPage version={version} /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}
