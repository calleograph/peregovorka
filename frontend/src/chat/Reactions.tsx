import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "../components/Icons";
import "./reactions.css";

export interface Reaction { emoji: string; count: number; user_ids: string[]; names: string[] }

/** Быстрые реакции (как в мессенджерах): частые значки и «+» — полный набор. Esc и щелчок вне — закрыть. */
export function ReactBar({ at, choices, current, onPick, onMore, onClose }: { at: DOMRect; choices: string[]; current: string | null; onPick: (e: string) => void; onMore: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: at.left, top: at.top - 46 });
  useLayoutEffect(() => {
    const b = ref.current?.getBoundingClientRect();
    if (b) setPos({ left: Math.min(Math.max(8, at.left), window.innerWidth - b.width - 8), top: at.top - b.height - 6 >= 8 ? at.top - b.height - 6 : at.bottom + 6 });
  }, [at]);
  useEffect(() => {
    const down = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key, true);
    return () => { window.removeEventListener("pointerdown", down, true); window.removeEventListener("keydown", key, true); };
  }, [onClose]);
  return (
    <div className="react-bar" ref={ref} role="menu" aria-label="Реакция" style={{ left: pos.left, top: pos.top }}>
      {choices.map((e) => <button key={e} type="button" role="menuitem" className={current === e ? "on" : ""} aria-label={e} onClick={() => onPick(e)}>{e}</button>)}
      <button type="button" role="menuitem" className="more" aria-label="Другие реакции" title="Другие реакции" onClick={onMore}><Icon name="plus" size={16} /></button>
    </div>
  );
}

/** Значки реакций под сообщением: число, кто поставил — в подсказке; своя — выделена; нажатие ставит или снимает такую же. */
export function ReactionChips({ items, me, onToggle }: { items: Reaction[] | undefined; me: string; onToggle?: (emoji: string) => void }) {
  if (!items?.length) return null;
  return (
    <div className="msg-reacts">
      {items.map((r) => {
        const on = r.user_ids.includes(me);
        return (
          <button key={r.emoji} type="button" className={`msg-react ${on ? "mine" : ""}`} aria-pressed={on} disabled={!onToggle}
                  title={r.names.join(", ") + (r.count > r.names.length ? ` и ещё ${r.count - r.names.length}` : "")} onClick={() => onToggle?.(r.emoji)}>
            <span>{r.emoji}</span><span className="n">{r.count}</span>
          </button>
        );
      })}
    </div>
  );
}

export const myReaction = (items: Reaction[] | undefined, me: string) => items?.find((r) => r.user_ids.includes(me))?.emoji ?? null;
