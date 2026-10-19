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
  /** Вложенное меню (как в классических контекстных меню): строка со стрелкой ▸; сама по себе ничего не выполняет. */
  submenu?: MenuItem[];
  /** Отметка ✓ у текущего значения (в подменю выбора режима). */
  checked?: boolean;
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
  const [subOf, setSubOf] = useState<number | null>(null);       // индекс строки, чьё подменю открыто
  const [subCur, setSubCur] = useState(0);
  const subRef = useRef<HTMLDivElement>(null);
  const narrow = window.innerWidth < 600;                       // на телефоне подменю раскрывается внутри меню, а не сбоку

  useLayoutEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setPos(placeMenu(open.x, open.y, r.width, r.height, window.innerWidth, window.innerHeight));
  }, [open, ask, subOf]);
  const mainItems = () => ref.current?.querySelectorAll<HTMLElement>(":scope > div > [role=menuitem]") ?? ([] as unknown as NodeListOf<HTMLElement>);
  useEffect(() => { if (subOf === null) mainItems()[cur]?.focus(); }, [cur, ask, subOf]);
  useEffect(() => { if (subOf !== null) subRef.current?.querySelectorAll<HTMLElement>("[role^=menuitem]")[subCur]?.focus(); }, [subOf, subCur]);
  useEffect(() => {
    const down = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); if (subOf !== null) setSubOf(null); else onClose(); return; }
      if (subOf !== null) {                                       // навигация внутри открытого подменю
        const m = subRef.current?.querySelectorAll("[role^=menuitem]").length ?? 0;
        if (e.key === "ArrowDown") { e.preventDefault(); setSubCur((c) => (c + 1) % m); }
        else if (e.key === "ArrowUp") { e.preventDefault(); setSubCur((c) => (c - 1 + m) % m); }
        else if (e.key === "ArrowLeft") { e.preventDefault(); setSubOf(null); }
        else if (e.key === "Home") { e.preventDefault(); setSubCur(0); }
        else if (e.key === "End") { e.preventDefault(); setSubCur(m - 1); }
        return;
      }
      const n = mainItems().length;
      if (e.key === "ArrowDown") { e.preventDefault(); setCur((c) => (c + 1) % n); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setCur((c) => (c - 1 + n) % n); }
      else if (e.key === "ArrowRight" && open.items[cur]?.submenu) { e.preventDefault(); openSub(cur); }
      else if (e.key === "Home") { e.preventDefault(); setCur(0); }
      else if (e.key === "End") { e.preventDefault(); setCur(n - 1); }
    };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key);
    window.addEventListener("blur", onClose);
    window.addEventListener("resize", onClose);
    window.addEventListener("scroll", onClose, true);
    return () => { window.removeEventListener("pointerdown", down, true); window.removeEventListener("keydown", key); window.removeEventListener("blur", onClose); window.removeEventListener("resize", onClose); window.removeEventListener("scroll", onClose, true); };
  }, [onClose, subOf, cur, open.items]);

  const openSub = (i: number) => { setCur(i); setSubOf(i); setSubCur(Math.max(0, open.items[i].submenu?.findIndex((x) => x.checked) ?? 0)); };
  const run = (it: MenuItem, i = -1) => { if (it.disabled) return; if (it.submenu) { if (subOf === i) setSubOf(null); else openSub(i); return; } if (it.confirm && ask?.id !== it.id) { setAsk(it); return; } onClose(); it.onSelect(); };
  const firstDanger = open.items.findIndex((i) => i.danger);
  return createPortal(
    <div className={`ctx-menu ${pos.left + (ref.current?.offsetWidth ?? 240) + 200 > window.innerWidth ? "flip" : ""}`} ref={ref} role="menu" style={{ left: pos.left, top: pos.top }} onContextMenu={(e) => e.preventDefault()}>
      {ask ? (
        <div className="ctx-confirm" role="group" aria-label="Подтверждение">
          <p>{ask.confirm}</p>
          <div className="row">
            <button type="button" role="menuitem" className="btn mini danger" onClick={() => { onClose(); ask.onSelect(); }}>{ask.label}</button>
            <button type="button" role="menuitem" className="btn mini ghost" onClick={() => setAsk(null)}>Отмена</button>
          </div>
        </div>
      ) : open.items.map((it, i) => (
        <div key={it.id} className="ctx-row">
          {i === firstDanger && i > 0 && <div className="ctx-sep" role="separator" />}
          <button type="button" role="menuitem" tabIndex={-1} disabled={it.disabled} className={`ctx-item ${it.danger ? "danger" : ""}`} title={it.hint}
                  aria-haspopup={it.submenu ? "menu" : undefined} aria-expanded={it.submenu ? subOf === i : undefined}
                  onClick={() => run(it, i)} onMouseEnter={() => { setCur(i); if (it.submenu && !narrow) openSub(i); else if (subOf !== null && !narrow) setSubOf(null); }}>
            <span className="ctx-ico" aria-hidden>{it.icon ? <Icon name={it.icon} size={15} /> : null}</span><span className="ctx-text">{it.label}</span>
            {it.submenu && <span className="ctx-arrow" aria-hidden>{narrow && subOf === i ? "▾" : "▸"}</span>}
          </button>
          {it.submenu && subOf === i && (
            <div className={`ctx-sub ${narrow ? "inline" : ""}`} ref={subRef} role="menu" aria-label={it.label}>
              {it.submenu.filter((x) => !x.hidden).map((x, j) => (
                <button key={x.id} type="button" role="menuitemradio" aria-checked={!!x.checked} tabIndex={-1} disabled={x.disabled} className="ctx-item" title={x.hint}
                        onClick={() => { if (x.disabled) return; onClose(); x.onSelect(); }} onMouseEnter={() => setSubCur(j)}>
                  <span className="ctx-ico" aria-hidden>{x.checked ? "✓" : ""}</span><span className="ctx-text">{x.label}</span>
                </button>))}
            </div>)}
        </div>
      ))}
    </div>, document.body);
}
