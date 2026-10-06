import { useEffect, useRef, useState, type ReactNode } from "react";

/** Небольшое выпадающее меню (закрывается кликом снаружи и по Escape). */
export default function Menu({ label, children, className = "btn", title }: { label: ReactNode; children: ReactNode; className?: string; title?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button type="button" className={className} aria-haspopup="menu" aria-expanded={open} title={title} onClick={() => setOpen((o) => !o)}>{label} ▾</button>
      {open && <div className="menu-pop" role="menu" onClick={() => setOpen(false)}>{children}</div>}
    </div>
  );
}
