import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/** Небольшое выпадающее меню (закрывается кликом снаружи, по Escape, при прокрутке и смене размера окна).
 *  Список позиционируется по окну (`position: fixed`), а не внутри родителя: иначе его обрезает любая прокручиваемая панель инструментов вокруг
 *  (так «Скачать» на общей доске открывалось невидимым внутри полосы с `overflow-x: auto`). */
export default function Menu({ label, children, className = "btn", title }: { label: ReactNode; children: ReactNode; className?: string; title?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<CSSProperties | undefined>();
  const toggle = () => {
    if (!open) {
      const r = ref.current?.getBoundingClientRect();
      if (r) setPos({ position: "fixed", top: r.bottom + 4, left: Math.max(8, Math.min(r.left, window.innerWidth - 236)), zIndex: 2000 });
    }
    setOpen((o) => !o);
  };
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); window.removeEventListener("resize", close); window.removeEventListener("scroll", close, true); };
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button type="button" className={className} aria-haspopup="menu" aria-expanded={open} title={title} onClick={toggle}>{label} ▾</button>
      {open && <div className="menu-pop" role="menu" style={pos} onClick={() => setOpen(false)}>{children}</div>}
    </div>
  );
}
