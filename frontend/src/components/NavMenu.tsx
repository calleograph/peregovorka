import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { focusable, nextIndex, type MenuItem } from "../navMenu";

/**
 * Выпадающее меню верхней панели. Клавиатура: Enter/Пробел/↓ открывают и ставят фокус на первый пункт, ↑/↓/Home/End двигают, Esc закрывает и возвращает фокус
 * на кнопку, Tab закрывает. Закрывается кликом снаружи. Анимация — только прозрачность и небольшой сдвиг (отключается при prefers-reduced-motion).
 * Пока в вкладке идёт встреча (`newTab`), ссылки открываются в новой вкладке: уход со страницы комнаты оборвал бы звонок.
 */
export default function NavMenu({ label, items, active = false, align = "left", newTab = false, className = "", title }:
  { label: ReactNode; items: MenuItem[]; active?: boolean; align?: "left" | "right"; newTab?: boolean; className?: string; title?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const id = useId();
  const idx = focusable(items);

  const focusItem = useCallback((n: number) => {
    const el = root.current?.querySelectorAll<HTMLElement>("[data-mi]")[n];
    el?.focus();
  }, []);
  const close = useCallback((back = false) => { setOpen(false); if (back) btn.current?.focus(); }, []);

  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away, { passive: true });
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("touchstart", away); };
  }, [open]);

  const openAndFocus = (last = false) => { setOpen(true); window.setTimeout(() => focusItem(last ? idx.length - 1 : 0), 20); };
  const onButtonKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); openAndFocus(e.key === "ArrowUp"); }
    else if (e.key === "Escape" && open) { e.preventDefault(); close(true); }
  };
  const onPanelKey = (e: React.KeyboardEvent) => {
    const els = Array.from(root.current?.querySelectorAll<HTMLElement>("[data-mi]") ?? []);
    const cur = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); close(true); return; }
    if (e.key === "Tab") { setOpen(false); return; }
    const n = nextIndex(e.key, cur, els.length);
    if (n !== null) { e.preventDefault(); els[n]?.focus(); }
  };

  return (
    <div className={`dd ${open ? "open" : ""} ${active ? "is-active" : ""} ${align === "right" ? "dd-right" : ""} ${className}`} ref={root}>
      <button ref={btn} type="button" className="dd-btn" aria-haspopup="menu" aria-expanded={open} aria-controls={id} title={title}
              onClick={() => setOpen((o) => !o)} onKeyDown={onButtonKey}>
        <span className="dd-label">{label}</span><span className="dd-caret" aria-hidden>▾</span>
      </button>
      <div id={id} className="dd-panel" role="menu" aria-hidden={!open} onKeyDown={onPanelKey}>
        {items.map((it) => {
          if (it.kind === "divider") return <div key={it.key} className="dd-sep" role="separator" />;
          if (it.kind === "text") return <div key={it.key} className="dd-text">{it.label}</div>;
          if (it.kind === "link") {
            return (
              <a key={it.key} data-mi role="menuitem" tabIndex={open ? 0 : -1} href={it.to} className="dd-item" target={newTab ? "_blank" : undefined} rel={newTab ? "noopener" : undefined}
                 onClick={(e) => { if (newTab || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) { setOpen(false); return; } e.preventDefault(); setOpen(false); navigate(it.to); }}>
                <span>{it.label}{newTab && <span aria-hidden> ↗</span>}</span>{it.hint && <span className="dd-hint">{it.hint}</span>}
              </a>
            );
          }
          return (
            <button key={it.key} data-mi role="menuitem" type="button" tabIndex={open ? 0 : -1} className={`dd-item ${it.danger ? "danger" : ""}`} disabled={it.disabled}
                    onClick={() => { setOpen(false); it.onSelect(); }}>
              <span>{it.label}</span>{it.hint && <span className="dd-hint">{it.hint}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
