import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { EMOJI_GROUPS, recentEmoji, rememberEmoji } from "./emoji";

/** Всплывающий выбор эмодзи у кнопки `anchor`: вкладки групп и «недавние»; с `only` — один набор (полный список реакций). Esc и щелчок вне — закрыть. */
export default function EmojiPicker({ anchor, onPick, onClose, only, title }: { anchor: DOMRect; onPick: (e: string) => void; onClose: () => void; only?: string[]; title?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const recent = recentEmoji();
  const [tab, setTab] = useState(only ? "only" : recent.length ? "recent" : EMOJI_GROUPS[0].id);
  const [pos, setPos] = useState({ left: anchor.left, top: anchor.top - 8 });
  useLayoutEffect(() => {
    const b = ref.current?.getBoundingClientRect();
    if (!b) return;
    const left = Math.min(Math.max(8, anchor.left), window.innerWidth - b.width - 8);
    const above = anchor.top - b.height - 6;
    setPos({ left, top: above >= 8 ? above : Math.min(anchor.bottom + 6, window.innerHeight - b.height - 8) });
  }, [anchor, tab]);
  useEffect(() => {
    const down = (e: PointerEvent) => { const t = e.target as HTMLElement; if (!ref.current?.contains(t) && !t.closest?.("[data-emoji-toggle]")) onClose(); };     // кнопка-переключатель закрывает сама
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key, true);
    return () => { window.removeEventListener("pointerdown", down, true); window.removeEventListener("keydown", key, true); };
  }, [onClose]);
  const items = only ?? (tab === "recent" ? recent : EMOJI_GROUPS.find((g) => g.id === tab)?.items ?? []);
  const pick = (e: string) => { if (!only) rememberEmoji(e); onPick(e); };
  return createPortal(
    <div className="emoji-pop" ref={ref} role="dialog" aria-label={title ?? "Эмодзи"} style={{ left: pos.left, top: pos.top }}>
      {!only && (
        <div className="emoji-tabs" role="tablist">
          {recent.length > 0 && <button type="button" role="tab" aria-selected={tab === "recent"} className={tab === "recent" ? "on" : ""} title="Недавние" onClick={() => setTab("recent")}>🕘</button>}
          {EMOJI_GROUPS.map((g) => <button key={g.id} type="button" role="tab" aria-selected={tab === g.id} className={tab === g.id ? "on" : ""} title={g.title} onClick={() => setTab(g.id)}>{g.icon}</button>)}
        </div>
      )}
      <div className="emoji-grid">
        {only && title && <div className="emoji-title">{title}</div>}
        {items.map((e) => <button key={e} type="button" onClick={() => pick(e)} aria-label={e}>{e}</button>)}
      </div>
    </div>, document.body);
}
