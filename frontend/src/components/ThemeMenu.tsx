import { THEMES, effectiveTheme, setUserTheme, useSite } from "../site";
import NavMenu from "./NavMenu";
import type { MenuItem } from "../navMenu";

/** Быстрое переключение темы в верхней зоне: тот же выбор, что и в личном кабинете («Оформление»), хранится в браузере и действует сразу. */
export default function ThemeMenu({ newTab = false }: { newTab?: boolean }) {
  const site = useSite();
  const cur = effectiveTheme(site);
  const items: MenuItem[] = THEMES.map((t) => ({ kind: "action", key: t.id, label: t.label, hint: t.hint, checked: cur === t.id, onSelect: () => setUserTheme(t.id) }));
  const now = THEMES.find((t) => t.id === cur);
  return (
    <NavMenu className="thememenu" align="right" items={items} newTab={newTab} title="Тема оформления"
             label={<><i className="theme-dot" style={{ background: `linear-gradient(135deg, ${now?.swatch[0]}, ${now?.swatch[2]})` }} aria-hidden /><span className="tlabel">Тема</span></>} />
  );
}
