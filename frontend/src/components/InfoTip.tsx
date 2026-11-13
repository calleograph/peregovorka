import { useEffect, useId, useRef, useState, type ReactNode } from "react";

const HOVER = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

/**
 * Значок «?» в круге (рисунок — в design.css, .infotip-btn) сразу после короткого названия параметра: подробное пояснение — во всплывающей подсказке, а не постоянным текстом на странице.
 * Компьютер — по наведению и по нажатию; телефон — по нажатию (наведения там нет). Закрывается повторным нажатием, Esc, нажатием вне подсказки.
 * Внутри `<label>` значок не переключает поле (событие гасится) и не отнимает у поля подпись: это не <button>, а элемент с ролью кнопки.
 * `doc` — ссылка «Подробнее» на статью вики.
 */
export default function InfoTip({ children, doc, label = "Пояснение", tone }: { children: ReactNode; doc?: string; label?: string; tone?: "danger" }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  const leave = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) { setOpen(false); setPinned(false); } };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); setPinned(false); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc, true); };
  }, [open]);
  useEffect(() => () => window.clearTimeout(leave.current), []);
  const toggle = () => { const next = !(open && pinned); setPinned(next); setOpen(next); };
  return (
    <span className={`infotip${tone ? ` infotip-${tone}` : ""}`} ref={box}
          onMouseEnter={HOVER ? () => { window.clearTimeout(leave.current); setOpen(true); } : undefined}
          onMouseLeave={HOVER ? () => { if (!pinned) leave.current = window.setTimeout(() => setOpen(false), 150); } : undefined}>
      {/* не <button>: внутри <label> кнопка стала бы связанным с меткой элементом и поле ввода потеряло бы подпись */}
      <span role="button" tabIndex={0} className="infotip-btn" aria-label={label} aria-expanded={open} aria-describedby={open ? id : undefined}
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(); }}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); toggle(); } }} />
      {open && (
        <span className="infotip-pop" role="tooltip" id={id}>
          {children}
          {doc && <> <a href={doc} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>Подробнее</a></>}
        </span>
      )}
    </span>
  );
}
