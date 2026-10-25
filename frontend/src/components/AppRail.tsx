import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { ChatNavButton } from "../chat/ChatNav";
import { useSite } from "../site";
import { Icon, type IconName } from "./Icons";
import NavMenu from "./NavMenu";
import type { MenuItem } from "../navMenu";

function RailLink({ to, end, icon, children }: { to: string; end?: boolean; icon: IconName; children: ReactNode }) {
  return <NavLink to={to} end={end} className="rail-item" title={String(children)}><Icon name={icon} size={22} /><span>{children}</span></NavLink>;
}

/** Левая узкая панель разделов (на телефоне — нижняя полоса). Пункты те же, что были в верхнем меню; «Чаты» — кнопка с счётчиком, «Администрирование» — быстрые разделы. */
export default function AppRail({ isAdmin, adminItems, adminActive }: { isAdmin: boolean; adminItems: MenuItem[]; adminActive: boolean }) {
  const site = useSite();
  const mark = site.assets.logo_compact ?? null;
  return (
    <aside className="rail" aria-label="Разделы">
      <Link to="/" className="rail-brand" title={site.name} aria-label={site.name}>
        {mark ? <img src={mark} alt="" draggable={false} /> : <span className="rail-mark" aria-hidden>{(site.short_name || site.name || "P").slice(0, 1).toUpperCase()}</span>}
      </Link>
      <nav aria-label="Основная навигация" className="rail-nav">
        <RailLink to="/" end icon="video">Переговорки</RailLink>
        <RailLink to="/history" icon="clock">История</RailLink>
        <ChatNavButton />
        {isAdmin && <NavMenu className="rail-admin" label={<><Icon name="gear" size={22} /><span>Админ</span></>} items={adminItems} active={adminActive} title="Администрирование" />}
      </nav>
    </aside>
  );
}
