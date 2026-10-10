import type { ReactNode } from "react";
import { Icon, type IconName } from "../Icons";

export type Tone = "neutral" | "on" | "off" | "danger" | "live" | "rec";

/**
 * Круглая кнопка-пиктограмма с подписью. Состояние читается без текста:
 *  - neutral — обычное действие; on — включено (мягкий акцент); off — выключено (мягкий красный, пиктограмма перечёркнута);
 *  - live — идёт процесс (показ экрана/запись): заполнена акцентом и «дышит»; danger — завершающее действие.
 * `pressed` передаётся скринридеру (aria-pressed); подпись и подсказка всегда есть.
 */
export default function RoundButton({ icon, label, title, tone = "neutral", pressed, disabled, onClick, pulse, children }: {
  icon: IconName; label: string; title?: string; tone?: Tone; pressed?: boolean; disabled?: boolean; pulse?: boolean; onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void; children?: ReactNode;
}) {
  return (
    <div className="rbtn-wrap">
      <button type="button" className={`rbtn ${tone} ${pulse ? "pulse" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={pressed} aria-label={label} title={title ?? label}>
        <Icon name={icon} size={24} />
      </button>
      <span className="rbtn-label">{label}</span>
      {children}
    </div>
  );
}
