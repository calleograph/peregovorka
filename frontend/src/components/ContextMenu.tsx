import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent as RMouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { placeMenu, shouldKeepNative } from "../menuPlace";
import { Icon, type IconName } from "./Icons";

export interface MenuItem {
  id: string; label: string; icon?: IconName; onSelect: () => void;
  /** Деструктивное действие: отделяется чертой и требует подтверждения (если задан `confirm`). */
  danger?: boolean; confirm?: string;
  /** Недоступное данному пользователю действие не показывается вовсе; `disabled` — только когда полезно показать причину. */
  hidden?: boolean; disabled?: boolean; hint?: string;
}

interface Open { x: number; y: number; items: MenuItem[] }

/** Лёгкое контекстное меню (правая кнопка или Shift+F10 / клавиша меню): у курсора, не выходит за экран, закрывается кликом вне / Esc, управляется стрелками.
 *  Перехватывается только на конкретных объектах; выделенный текст, поля ввода и Shift+ПКМ остаются за браузером. Любое действие меню доступно и без него. */
export function useContextMenu(): { onContextMenu: (items: MenuItem[] | (() => MenuItem[])) => (e: RMouseEvent) => void; node: ReactNode; openAt: (x: number, y: number, items: MenuItem[]) => void } {
  const [open, setOpen] = useState<Open | null>(null);
  const openAt = useCallback((x: number, y: number, items: MenuItem[]) => { const vis = items.filter((i) => !i.hidden); if (vis.length) setOpen({ x, y, items: vis }); }, []);
  const onContextMenu = useCallback((items: MenuItem[] | (() => MenuItem[])) => (e: RMouseEvent) => {
    const t = e.target as HTMLElement;
    if (shouldKeepNative({ shift: e.shiftKey, targetTag: t.tagName, selection: window.getSelection()?.toString() ?? "" })) return;
    e.preventDefault();
    const x = e.clientX || t.getBoundingClientRect().left + 8, y = e.clientY || t.getBoundingClientRect().top + 8;     // с клавиатуры (Shift+F10) координат нет
    openAt(x, y, typeof items === "function" ? items() : items);
  }, [openAt]);
  const node = open ? <MenuView open={open} onClose={() => setOpen(null)} /> : null;
  return { onContextMenu, node, openAt };
}

function MenuView({ open, onClose }: { open: Open; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: open.x, top: open.y });
  const [ask, setAsk] = useState<MenuItem | null>(null);
  const [cur, setCur] = useState(0);

  useLayoutEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setPos(placeMenu(open.x, open.y, r.width, r.height, window.innerWidth, window.innerHeight));
  }, [open, ask]);
  useEffect(() => { ref.current?.querySelectorAll<HTMLElement>("[role=menuitem]")[cur]?.focus(); }, [cur, ask]);
  useEffect(() => {
    const down = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      const n = ref.current?.querySelectorAll("[role=menuitem]").length ?? 0;
      if (e.key === "ArrowDown") { e.preventDefault(); setCur((c) => (c + 1) % n); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setCur((c) => (c - 1 + n) % n); }
      else if (e.key === "Home") { e.preventDefault(); setCur(0); }
      else if (e.key === "End") { e.preventDefault(); setCur(n - 1); }
    };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key);
    window.addEventListener("blur", onClose);
    window.addEventListener("resize", onClose);
    window.addEventListener("scroll", onClose, true);
    return () => { window.removeEventListener("pointerdown", down, true); window.removeEventListener("keydown", key); window.removeEventListener("blur", onClose); window.removeEventListener("resize", onClose); window.removeEventListener("scroll", onClose, true); };
  }, [onClose]);

  const run = (it: MenuItem) => { if (it.disabled) return; if (it.confirm && ask?.id !== it.id) { setAsk(it); return; } onClose(); it.onSelect(); };
  const firstDanger = open.items.findIndex((i) => i.danger);
  return createPortal(
    <div className="ctx-menu" ref={ref} role="menu" style={{ left: pos.left, top: pos.top }} onContextMenu={(e) => e.preventDefault()}>
      {ask ? (
        <div className="ctx-confirm" role="group" aria-label="Подтверждение">
          <p>{ask.confirm}</p>
          <div className="row">
            <button type="button" role="menuitem" className="btn mini danger" onClick={() => { onClose(); ask.onSelect(); }}>{ask.label}</button>
            <button type="button" role="menuitem" className="btn mini ghost" onClick={() => setAsk(null)}>Отмена</button>
          </div>
        </div>
      ) : open.items.map((it, i) => (
        <div key={it.id}>
          {i === firstDanger && i > 0 && <div className="ctx-sep" role="separator" />}
          <button type="button" role="menuitem" tabIndex={-1} disabled={it.disabled} className={`ctx-item ${it.danger ? "danger" : ""}`} title={it.hint}
                  onClick={() => run(it)} onMouseEnter={() => setCur(i)}>
            <span className="ctx-ico" aria-hidden>{it.icon ? <Icon name={it.icon} size={15} /> : null}</span>{it.label}
          </button>
        </div>
      ))}
    </div>, document.body);
}
