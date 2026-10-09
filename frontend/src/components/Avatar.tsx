import type { CSSProperties } from "react";
import { initials, tint } from "../fx";

/** Круглая аватарка: картинка пользователя или инициалы на цветном фоне (цвет зависит только от имени). */
export default function Avatar({ name, url, size = 32, className = "" }: { name: string; url?: string | null; size?: number; className?: string }) {
  const t = tint(name || "?");
  const style = { width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.4)), "--ha": t.a, "--hb": t.b } as CSSProperties;
  return url
    ? <img className={`av ${className}`} src={url} alt="" width={size} height={size} style={style} loading="lazy" />
    : <span className={`av av-init ${className}`} style={style} aria-hidden>{initials(name || "?")}</span>;
}
