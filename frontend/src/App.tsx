import { Suspense, lazy, useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, setCsrf, setUnauthorizedHandler, type Me, type Profile } from "./api";
import Avatar from "./components/Avatar";
import { AppFooter, CookieNotice, type BuildInfo } from "./components/ProductInfo";
import { versionLabel } from "./util";
import NavMenu from "./components/NavMenu";
import { ADMIN_QUICK, adminHref, type MenuItem } from "./navMenu";
import { useActiveMeeting } from "./activeMeeting";
import GuestPage from "./pages/GuestPage";
import HistoryPage from "./pages/HistoryPage";
import LoginPage from "./pages/LoginPage";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import SetupWizard from "./components/SetupWizard";
import MeetingPage from "./pages/MeetingPage";
import RoomsPage from "./pages/RoomsPage";

// Тяжёлые части (livekit-client, админка) грузятся только когда нужны.
const RoomRoute = lazy(() => import("./pages/RoomRoute"));
const ProfilePage = lazy(() => import("./pages/ProfilePage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));

/** Раздел верхней панели. Пока в этой вкладке идёт встреча, раздел открывается в НОВОЙ вкладке: уход со страницы комнаты оборвал бы звонок. */
function NavItem({ to, end, newTab, children }: { to: string; end?: boolean; newTab: boolean; children: ReactNode }) {
  if (newTab) return <a href={to} target="_blank" rel="noopener" className="nav-newtab" title="Откроется в новой вкладке — текущая встреча не прервётся">{children}<span aria-hidden> ↗</span></a>;
  return <NavLink to={to} end={end}>{children}</NavLink>;
}

function StaffApp() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined — ещё проверяем сессию
  const [version, setVersion] = useState("");
  const [build, setBuild] = useState<BuildInfo | null>(null);
  const [wizard, setWizard] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [burger, setBurger] = useState(false);
  const inMeeting = useActiveMeeting() !== null;
  useEffect(() => setBurger(false), [pathname]);   // на малых экранах меню закрывается при переходе
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);        // аватарка в верхнем меню; хуки — до любых ранних return
  const myId = me ? me.user.id : null;
  useEffect(() => { if (myId) void api.profile().then((p) => setAvatarUrl(p.avatar_url)).catch(() => undefined); }, [myId]);
  const onProfile = useCallback((p: Profile) => setAvatarUrl(p.avatar_url), []);

  useEffect(() => {
    api.me().then((m) => { setCsrf(m.csrf_token); setMe(m); }).catch(() => setMe(null));
    api.version().then((v) => { setVersion(versionLabel(v.version, v.commit)); setBuild({ version: v.version, commit: v.commit }); }).catch(() => undefined);
  }, []);

  useEffect(() => { setUnauthorizedHandler(() => { setCsrf(""); setMe(null); }); return () => setUnauthorizedHandler(null); }, []);

  const onLogin = useCallback((m: Me) => { setCsrf(m.csrf_token); setMe(m); navigate("/"); }, [navigate]);
  const logout = async () => { await api.logout().catch(() => undefined); setCsrf(""); setMe(null); navigate("/"); };

  // мастер первоначальной настройки — один раз после первого локального входа (пока не завершён и не закрыт)
  useEffect(() => {
    if (!me || me.must_change_password || !me.user.is_admin || !me.local) { setWizard(false); return; }
    void api.admin.setupStatus().then((s) => setWizard(s.show)).catch(() => undefined);
  }, [me]);
  const goAdmin = (page: string) => {
    try { sessionStorage.setItem("adminTab", page); } catch { /* ignore */ }
    navigate("/admin");
    window.setTimeout(() => window.dispatchEvent(new CustomEvent("admin:goto", { detail: page })), 50);
  };

  if (me === undefined) return <div className="center muted">Загрузка…</div>;
  if (me === null) return <LoginPage onLogin={onLogin} info={build} />;
  if (me.must_change_password) return <ChangePasswordPage onDone={() => { void api.me().then((m) => { setCsrf(m.csrf_token); setMe(m); }); }} onLogout={logout} />;

  // Верхнее меню — для повседневной работы; полный список разделов администрирования остаётся в левом меню самой админки
  const adminItems: MenuItem[] = [
    ...ADMIN_QUICK.map((q) => ({ kind: "link" as const, key: q.tab, label: q.label, to: adminHref(q.tab), hint: q.hint })),
    { kind: "divider", key: "sep" },
    { kind: "link", key: "all", label: "Все разделы администрирования →", to: "/admin" },
  ];
  const userItems: MenuItem[] = [
    { kind: "text", key: "who", label: me.user.is_admin ? "Администратор системы" : "Пользователь" },
    { kind: "link", key: "profile", label: "Личный кабинет", to: "/profile", hint: "профиль, аватарка, данные из AD" },
    { kind: "link", key: "hist", label: "История моих встреч", to: "/history" },
    { kind: "divider", key: "sep" },
    ...(version ? [{ kind: "text" as const, key: "ver", label: `Версия ${version}` }] : []),
    { kind: "action", key: "out", label: "Выйти", danger: true, disabled: inMeeting, hint: inMeeting ? "сначала выйдите из комнаты" : undefined, onSelect: () => void logout() },
  ];

  return (
    <div className="shell">
      <header className={`topbar ${burger ? "burger-open" : ""}`}>
        {inMeeting
          ? <a href="/" target="_blank" rel="noopener" className="brand" title="Откроется в новой вкладке">Peregovorka ↗</a>
          : <Link to="/" className="brand">Peregovorka</Link>}
        <button type="button" className="burger" aria-label="Меню" aria-expanded={burger} onClick={() => setBurger((b) => !b)}><span /><span /><span /></button>
        <nav aria-label="Основная навигация" className="topnav">
          <NavItem to="/" end newTab={inMeeting}>Переговорки</NavItem>
          <NavItem to="/history" newTab={inMeeting}>История</NavItem>
          {me.user.is_admin && <NavMenu label="Администрирование" items={adminItems} active={pathname.startsWith("/admin")} newTab={inMeeting} />}
        </nav>
        <div className="spacer" />
        <NavMenu className="usermenu" align="right" items={userItems} newTab={inMeeting} title={me.user.display_name}
                 label={<><Avatar name={me.user.display_name} url={avatarUrl} size={28} /><span className="uname">{me.user.display_name}</span></>} />
      </header>
      <main>
        {wizard && <SetupWizard onGo={goAdmin} onClose={() => setWizard(false)} />}
        <Suspense fallback={<div className="muted">Загрузка…</div>}>
        <Routes>
          <Route path="/" element={<RoomsPage />} />
          <Route path="/rooms/:roomId" element={<RoomRoute selfName={me.user.display_name} />} />
          <Route path="/profile" element={<ProfilePage onChanged={onProfile} />} />
          <Route path="/history" element={<HistoryPage isAdmin={me.user.is_admin} />} />
          <Route path="/history/:meetingId" element={<MeetingPage isAdmin={me.user.is_admin} />} />
          <Route path="/admin" element={me.user.is_admin ? <AdminPage version={version} /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
      {!pathname.startsWith("/rooms/") && <AppFooter info={build} />}
      <CookieNotice />
    </div>
  );
}

/** Гостевая оболочка: без входа в систему, без разделов и истории — только страница гостя. */
function GuestApp() {
  return (
    <div className="shell guest-shell">
      <header className="topbar"><span className="brand">Peregovorka</span><span className="muted">Гостевой доступ</span></header>
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
