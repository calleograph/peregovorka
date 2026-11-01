import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { ChatNavButton } from "../chat/ChatNav";
import { useSite } from "../site";
import { useCalendarAvailable } from "../useCalendarAvailable";
import { Icon, type IconName } from "./Icons";
import { adminHref } from "../navMenu";

function RailLink({ to, end, icon, children }: { to: string; end?: boolean; icon: IconName; children: ReactNode }) {
  return <NavLink to={to} end={end} className="rail-item" title={String(children)}><Icon name={icon} size={22} /><span>{children}</span></NavLink>;
}

/** Левая узкая панель разделов (на телефоне — нижняя полоса). «Чаты» — кнопка со счётчиком; «Админ» — сразу «Обзор» администрирования (без промежуточного меню). */
export default function AppRail({ isAdmin }: { isAdmin: boolean }) {
  const site = useSite();
  const mark = site.assets.logo_compact ?? null;
  const calendar = useCalendarAvailable();
  return (
    <aside className="rail" aria-label="Разделы">
      <Link to="/" className="rail-brand" title={site.name} aria-label={site.name}>
        {mark ? <img src={mark} alt="" draggable={false} /> : <span className="rail-mark" aria-hidden>{(site.short_name || site.name || "P").slice(0, 1).toUpperCase()}</span>}
      </Link>
      <nav aria-label="Основная навигация" className="rail-nav">
        <RailLink to="/" end icon="video">Переговорки</RailLink>
        {calendar && <RailLink to="/calendar" icon="calendar">Календарь</RailLink>}
        <RailLink to="/history" icon="clock">История</RailLink>
        <ChatNavButton />
        {isAdmin && <RailLink to={adminHref("system")} icon="gear">Админ</RailLink>}
      </nav>
    </aside>
  );
}
