import { watchTables } from "./stackTables";
import { Suspense, lazy, useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, isRestricted, onRestricted, setCsrf, setUnauthorizedHandler, type Me, type Profile } from "./api";
import RestrictedPage from "./pages/RestrictedPage";
import Avatar from "./components/Avatar";
import { AppFooter, CookieNotice, type BuildInfo } from "./components/ProductInfo";
import PrivacyPage from "./pages/PrivacyPage";
import LegalPage from "./pages/LegalPage";
import ConsentGate from "./components/ConsentGate";
import HelpDialog from "./components/HelpDialog";
import { Brand } from "./components/Brand";
import { loadSite, useSite } from "./site";
import { versionLabel } from "./util";
import { applyFavicon, pageTitle } from "./pageTitle";
import NavMenu from "./components/NavMenu";
import AppRail from "./components/AppRail";
import ThemeMenu from "./components/ThemeMenu";
import { ADMIN_QUICK, adminHref, type MenuItem } from "./navMenu";
import { useActiveMeeting } from "./activeMeeting";
import GuestPage from "./pages/GuestPage";
import HistoryPage from "./pages/HistoryPage";
import LoginPage from "./pages/LoginPage";
import TotpEnrollGate from "./components/TotpEnrollGate";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import SetupWizard from "./components/SetupWizard";
import MeetingPage from "./pages/MeetingPage";
import RoomsPage from "./pages/RoomsPage";
import { ChatHost, ChatNavButton } from "./chat/ChatNav";
import { chats } from "./chat/chatStore";
import { onDesktopCommand } from "./desktop";
import { watchMeetings } from "./desktopMeetings";

// Тяжёлые части (livekit-client, админка) грузятся только когда нужны.
import OutlookCenter from "./pages/OutlookCenter";
const RoomRoute = lazy(() => import("./pages/RoomRoute"));
const CalendarPage = lazy(() => import("./pages/CalendarPage"));
import PeerOpenPage from "./pages/PeerOpenPage";
import MeetPage from "./pages/MeetPage";
import CaptchaHost from "./components/CaptchaDialog";
const ProfilePage = lazy(() => import("./pages/ProfilePage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));
const PlayerHost = lazy(() => import("./player/PlayerHost"));
const AudienceDemo = import.meta.env.DEV ? lazy(() => import("./dev/AudienceDemo")) : null;      // только `npm run dev`: проверка списка зрителей на тысяче синтетических людей

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
  const site = useSite();
  const [help, setHelp] = useState(false);
  const [pending, setPending] = useState<{ kind: string; title: string; version: number }[]>([]);
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [burger, setBurger] = useState(false);
  const inMeeting = useActiveMeeting() !== null;
  useEffect(() => setBurger(false), [pathname]);
  useEffect(() => watchTables(), []);                                        // узкий экран: таблицы — карточками (stackTables.ts)
  // приложение Windows: переход по уведомлению и пункт «Чаты» в трее; `?chat=<id>` открывает переписку (ссылка из уведомления)
  useEffect(() => {
    const a = onDesktopCommand("navigate", (c) => { if (c.route?.startsWith("/") && !c.route.startsWith("//")) navigate(c.route); });
    const b = onDesktopCommand("chats", () => chats.openWindow());
    return () => { a(); b(); };
  }, [navigate]);
  useEffect(() => {
    const id = new URLSearchParams(search).get("chat");
    if (!id || !me) return;
    chats.openWindow(id);
    navigate(pathname, { replace: true });
  }, [search, pathname, me, navigate]);
  useEffect(() => { document.title = pageTitle(pathname, me !== null, site.name); }, [pathname, me, site.name]);
  useEffect(() => { applyFavicon(inMeeting); return () => applyFavicon(false); }, [inMeeting]);   // во время встречи у значка вкладки красная точка   // на малых экранах меню закрывается при переходе
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);        // аватарка в верхнем меню; хуки — до любых ранних return
  const myId = me ? me.user.id : null;
  useEffect(() => (myId ? watchMeetings() : undefined), [myId]);           // приложение Windows: уведомления о встречах (в браузере — ничего)
  useEffect(() => { if (myId) void api.profile().then((p) => setAvatarUrl(p.avatar_url)).catch(() => undefined); }, [myId]);
  const onProfile = useCallback((p: Profile) => setAvatarUrl(p.avatar_url), []);

  useEffect(() => {
    api.me().then((m) => { setCsrf(m.csrf_token); setMe(m); }).catch(() => setMe(null));
    api.version().then((v) => { setVersion(versionLabel(v.version, v.commit)); setBuild({ version: v.version, commit: v.commit }); }).catch(() => undefined);
  }, []);

  // подтверждение документов организации (если включено): после входа и при выходе новой редакции; контакты поддержки зависят от входа — перечитываем оформление
  const myUid = me ? me.user.id : null;
  useEffect(() => {
    if (!myUid) { setPending([]); return; }
    void loadSite();
    void api.legal.pending().then((r) => setPending(r.items)).catch(() => undefined);
  }, [myUid]);

  useEffect(() => { setUnauthorizedHandler(() => { setCsrf(""); setMe(null); }); return () => setUnauthorizedHandler(null); }, []);

  const onLogin = useCallback((m: Me) => {
    setCsrf(m.csrf_token); setMe(m);
    const next = new URLSearchParams(window.location.search).get("next") ?? "";
    // возврат после входа: окно входа надстройки Outlook или страница конференции из календаря (только эти адреса — открытого перенаправления нет)
    if (next === "/outlook/auth.html") { window.location.assign(next); return; }
    if (/^\/m\/[a-z0-9]{20,32}$/.test(next)) { navigate(next); return; }
    if (!window.location.pathname.startsWith("/p/")) navigate("/");
  }, [navigate]);        // ссылка на равноправную комнату после входа открывается, а не теряется
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
  if (pending.length > 0 && !me.must_change_password) return <ConsentGate items={pending} onDone={() => setPending([])} onLogout={logout} />;
  if (me.must_enroll_totp && !me.must_change_password) return <TotpEnrollGate onDone={() => { void api.me().then((m) => { setCsrf(m.csrf_token); setMe(m); }); }} onLogout={logout} />;
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
    ...(site.support || site.documents.length > 0 ? [{ kind: "action" as const, key: "help", label: "Помощь и поддержка", hint: "контакты, документы организации", onSelect: () => setHelp(true) }] : []),
    { kind: "divider", key: "sep" },
    ...(version ? [{ kind: "text" as const, key: "ver", label: `Версия ${version}` }] : []),
    { kind: "action", key: "out", label: "Выйти", danger: true, disabled: inMeeting, hint: inMeeting ? "сначала выйдите из комнаты" : undefined, onSelect: () => void logout() },
  ];

  const rail = !inMeeting;            // во встрече остаётся прежняя верхняя панель (разделы открываются в новой вкладке); вне встречи — левая панель разделов
  const userMenu = (
    <NavMenu className="usermenu" align="right" items={userItems} newTab={inMeeting} title={me.user.display_name}
             label={<><Avatar name={me.user.display_name} url={avatarUrl} size={28} /><span className="uname">{me.user.display_name}</span></>} />
  );
  return (
    <div className={`shell ${rail ? "has-rail" : ""}`}>
      {rail && <AppRail isAdmin={me.user.is_admin} adminItems={adminItems} adminActive={pathname.startsWith("/admin")} />}
      <div className="shell-col">
      {rail ? (
        <header className="topbar">
          <Link to="/" className="brand topbar-brand"><Brand /></Link>
          <div className="spacer" />
          <ThemeMenu />
          {userMenu}
        </header>
      ) : (
        <header className={`topbar ${burger ? "burger-open" : ""}`}>
          <a href="/" target="_blank" rel="noopener" className="brand" title="Откроется в новой вкладке"><Brand suffix=" ↗" /></a>
          <button type="button" className="burger" aria-label="Меню" aria-expanded={burger} onClick={() => setBurger((b) => !b)}><span /><span /><span /></button>
          <nav aria-label="Основная навигация" className="topnav">
            <NavItem to="/" end newTab>Переговорки</NavItem>
            <NavItem to="/history" newTab>История</NavItem>
            <ChatNavButton />
            {me.user.is_admin && <NavMenu label="Администрирование" items={adminItems} active={pathname.startsWith("/admin")} newTab />}
          </nav>
          <div className="spacer" />
          {userMenu}
        </header>
      )}
      <main>
        {wizard && <SetupWizard onGo={goAdmin} onClose={() => setWizard(false)} />}
        <Suspense fallback={<div className="muted">Загрузка…</div>}>
        <Routes>
          <Route path="/" element={<RoomsPage isAdmin={me.user.is_admin} />} />
          <Route path="/rooms/:roomId" element={<RoomRoute selfName={me.user.display_name} />} />
          <Route path="/p/:code" element={<PeerOpenPage />} />
          {AudienceDemo && <Route path="/__dev/audience" element={<Suspense fallback={null}><AudienceDemo /></Suspense>} />}
          <Route path="/profile" element={<ProfilePage onChanged={onProfile} />} />
          <Route path="/calendar" element={<Suspense fallback={null}><CalendarPage /></Suspense>} />
          <Route path="/history" element={<HistoryPage isAdmin={me.user.is_admin} />} />
          <Route path="/history/:meetingId" element={<MeetingPage isAdmin={me.user.is_admin} />} />
          <Route path="/admin" element={me.user.is_admin ? <AdminPage version={version} /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
      <Suspense fallback={null}><PlayerHost /></Suspense>
      {!pathname.startsWith("/rooms/") && <AppFooter info={build} />}
      </div>
      {help && <HelpDialog onClose={() => setHelp(false)} />}
      <ChatHost userId={me.user.id} />
      <CookieNotice />
    </div>
  );
}

/** Гостевая оболочка: без входа в систему, без разделов и истории — только страница гостя. */
function GuestApp() {
  return (
    <div className="shell guest-shell">
      <header className="topbar"><span className="brand"><Brand /></span><span className="muted">Гостевой доступ</span></header>
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
  const [blocked, setBlocked] = useState(isRestricted());
  useEffect(() => onRestricted(() => setBlocked(true)), []);
  if (blocked) return <RestrictedPage />;          // адрес вне разрешённых подсетей: вместо интерфейса — нейтральная страница
  return <><CaptchaHost /><Pages /></>;       // окно проверки «я не робот» живёт в корне: его вызывает любой запрос, на который сервер ответил 428
}

function Pages() {
  const { pathname } = useLocation();
  const site = useSite();
  useEffect(() => { if (pathname === "/privacy" || pathname.startsWith("/guest/")) document.title = pageTitle(pathname, false, site.name); }, [pathname, site.name]);
  if (pathname.startsWith("/legal/")) return <><Routes><Route path="/legal/:kind" element={<LegalPage />} /></Routes><CookieNotice /></>;       // документы организации открываются без входа
  if (pathname === "/outlook" || pathname === "/outlook/") return <Routes><Route path="*" element={<OutlookCenter />} /></Routes>;          // центр установки надстройки: открывается без входа
  if (pathname.startsWith("/m/")) return <Routes><Route path="/m/:code" element={<MeetPage />} /></Routes>;          // страница конференции из календаря: открывается без входа
  if (pathname === "/privacy") return <><PrivacyPage /><CookieNotice /></>;          // открывается без входа (ссылка «Подробнее» на странице входа)
  return pathname.startsWith("/guest/") ? <GuestApp /> : <StaffApp />;
}
