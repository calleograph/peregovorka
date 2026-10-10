import type { ReactNode } from "react";
import { Icon, type IconName } from "../Icons";

export type Tone = "neutral" | "on" | "off" | "danger" | "live" | "rec";

/**
 * Круглая кнопка-пиктограмма с подписью. Состояние читается без текста:
 *  - neutral — обычное действие; on — включено (мягкий акцент); off — выключено (мягкий красный, пиктограмма перечёркнута);
 *  - live — идёт процесс (показ экрана/запись): заполнена акцентом и «дышит»; danger — завершающее действие.
 * `pressed` передаётся скринридеру (aria-pressed); `label` — полное название (для скринридера и поиска), `short` — короткая подпись под кнопкой
 * (ячейки панели имеют фиксированную ширину, поэтому подпись не должна меняться по длине); `title` — подсказка с пояснением.
 * `onMore` добавляет маленькую кнопку-стрелку у края (например, выбор устройства рядом с микрофоном).
 */
export default function RoundButton({ icon, label, short, title, tone = "neutral", pressed, disabled, onClick, pulse, badge, onMore, moreLabel, children }: {
  icon: IconName; label: string; short?: string; title?: string; tone?: Tone; pressed?: boolean; disabled?: boolean; pulse?: boolean; badge?: boolean;
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void; onMore?: (e: React.MouseEvent<HTMLButtonElement>) => void; moreLabel?: string; children?: ReactNode;
}) {
  return (
    <div className="rbtn-wrap">
      <div className="rbtn-box">
        <button type="button" className={`rbtn ${tone} ${pulse ? "speak" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={pressed} aria-label={label} title={title ?? label}>
          <Icon name={icon} size={20} />
        </button>
        {badge && <span className="rbtn-badge" aria-hidden />}
        {onMore && <button type="button" className="rbtn-more" onClick={onMore} aria-label={moreLabel ?? "Выбор устройства"} title={moreLabel ?? "Выбор устройства"} aria-haspopup="dialog"><Icon name="chevronD" size={11} /></button>}
      </div>
      <span className="rbtn-label" aria-hidden>{short ?? label}</span>
      {children}
    </div>
  );
}
