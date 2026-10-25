import type { CSSProperties } from "react";
import { initials, tint } from "../fx";
import { useImageFallback } from "./useImageFallback";

/** Круглая аватарка: картинка пользователя или инициалы на цветном фоне (цвет зависит только от имени). */
export default function Avatar({ name, url, size = 32, className = "" }: { name: string; url?: string | null; size?: number; className?: string }) {
  const img = useImageFallback(url);
  const t = tint(name || "?");
  const style = { width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.4)), "--ha": t.a, "--hb": t.b } as CSSProperties;
  return url && !img.failed
    ? <img className={`av ${className}`} src={img.src ?? url} alt="" width={size} height={size} style={style} loading="lazy" onError={img.onError} />
    : <span className={`av av-init ${className}`} style={style} aria-hidden>{initials(name || "?")}</span>;
}
