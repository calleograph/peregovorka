import { useSite } from "../site";

/** Название системы с логотипом (если организация его загрузила). `compact` — компактный логотип для тесных мест (верхняя панель на телефоне). Единый источник — `useSite()`. */
export function Brand({ compact = false, suffix, name = false }: { compact?: boolean; suffix?: string; name?: boolean }) {
  const s = useSite();
  const src = compact ? s.assets.logo_compact ?? s.assets.logo : s.assets.logo ?? s.assets.logo_compact;
  return (
    <>
      {src ? <img className={`brand-logo ${compact ? "compact" : ""}`} src={src} alt={name ? "" : s.name} draggable={false} /> : null}
      {(!src || name) && <span className="brand-name">{compact ? s.short_name : s.name}</span>}
      {suffix}
    </>
  );
}
