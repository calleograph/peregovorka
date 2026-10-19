import type { CSSProperties } from "react";
import type { RoomIconInfo } from "../api";
import { initials, tint } from "../fx";
import { iconByKey } from "../roomIcons/search";

/**
 * Значок комнаты везде одинаково: загруженная картинка, значок из библиотеки на цветной плитке или стандартный вариант (цветной квадрат с одной–двумя буквами названия).
 * Цвет плитки зависит от названия, как и раньше. Картинка адресуется с версией — при замене кэш браузера обновляется сам.
 */
export default function RoomIcon({ name, icon, size = 46, className = "" }: { name: string; icon?: RoomIconInfo | null; size?: number; className?: string }) {
  const t = tint(name);
  const style = { width: size, height: size, borderRadius: Math.round(size * 0.28), "--ha": t.a, "--hb": t.b, fontSize: Math.round(size * 0.35) } as CSSProperties;
  if (icon?.kind === "image" && icon.url) {
    return <img className={`room-icon img ${className}`} src={icon.url} width={size} height={size} alt="" aria-hidden loading="lazy" decoding="async" style={style} />;
  }
  const def = icon?.kind === "library" ? iconByKey(icon.key) : undefined;
  if (def) {
    return (
      <span className={`room-icon lib ${className}`} style={style} aria-hidden>
        <svg viewBox="0 0 24 24" width={Math.round(size * 0.58)} height={Math.round(size * 0.58)} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
             dangerouslySetInnerHTML={{ __html: def.body }} />
      </span>
    );
  }
  return <span className={`room-icon std ${className}`} style={style} aria-hidden>{initials(name)}</span>;
}
