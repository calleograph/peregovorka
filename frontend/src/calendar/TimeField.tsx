import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../components/Icons";

const STEP = 15;
const pad = (n: number) => String(n).padStart(2, "0");
const SLOTS = Array.from({ length: (24 * 60) / STEP }, (_, i) => `${pad(Math.floor((i * STEP) / 60))}:${pad((i * STEP) % 60)}`);
const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));

/** Введённое время словами человека → «ЧЧ:ММ» (24 часа): «9» → 09:00, «930» → 09:30, «9:5» → 09:05, «0930». Не время — null. */
export function parseTime(raw: string): string | null {
  const s = raw.trim();
  let h: string, m: string;
  if (/^\d{1,2}[:.]\d{0,2}$/.test(s)) { [h, m] = s.split(/[:.]/); m = m === "" ? "00" : m; }
  else if (/^\d{1,2}$/.test(s)) { h = s; m = "00"; }
  else if (/^\d{3,4}$/.test(s)) { h = s.slice(0, s.length - 2); m = s.slice(-2); }
  else return null;
  const hh = Number(h), mm = Number(m);
  return hh > 23 || mm > 59 ? null : `${pad(hh)}:${pad(mm)}`;
}

/** Ввод набирается цифрами, двоеточие подставляется само: «0930» и «930» → «09:30», «93» → «09:3» (час не бывает больше 23 — первая цифра становится часом). Четыре цифры вставкой остаются как есть: «2575» не время. */
const mask = (v: string, prev: string): string => {
  let s = v.replace(/[^\d:]/g, "").slice(0, 5);
  if (!s.includes(":")) {
    const d = s;
    if (d.length === 4) s = `${d.slice(0, 2)}:${d.slice(2)}`;
    else if (d.length === 3 || (d.length === 2 && d.length > prev.length)) {
      const shift = Number(d.slice(0, 2)) > 23;
      s = shift ? `0${d[0]}:${d.slice(1)}` : `${d.slice(0, 2)}:${d.slice(2)}`;
    }
  }
  return s;
};

/** Время в 24-часовом формате: поле с вводом с клавиатуры и список через каждые 15 минут. Список рисуется поверх страницы (не обрезается окном) и следует за прокруткой. */
export function TimeField({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const [draft, setDraft] = useState(value);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const [pos, setPos] = useState<{ left: number; width: number; top?: number; bottom?: number } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => setDraft(value), [value]);
  const bad = draft !== value && parseTime(draft) === null;

  const place = () => {
    const r = input.current?.getBoundingClientRect();
    if (!r) return;
    const below = window.innerHeight - r.bottom, up = below < 230 && r.top > below;
    setPos({ left: r.left, width: Math.max(r.width, 112), ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }) });
  };
  useLayoutEffect(() => { if (open) place(); }, [open]);        // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return;
    const f = () => place();
    window.addEventListener("scroll", f, true); window.addEventListener("resize", f);
    return () => { window.removeEventListener("scroll", f, true); window.removeEventListener("resize", f); };
  }, [open]);
  // выбранное время — в середине списка
  useLayoutEffect(() => {
    if (!open || !pos) return;
    const el = list.current?.querySelector<HTMLElement>("[data-cur]");
    if (el && list.current) list.current.scrollTop = el.offsetTop - list.current.clientHeight / 2 + el.offsetHeight / 2;
  }, [open, !!pos]);        // eslint-disable-line react-hooks/exhaustive-deps

  const commit = (v: string) => { setDraft(v); if (v !== value) onChange(v); };
  const nearest = (t: string) => { const m = toMin(t); return Math.min(SLOTS.length - 1, Math.max(0, Math.round(m / STEP))); };
  const pick = (t: string) => { commit(t); setOpen(false); input.current?.focus(); };
  const settle = () => { const p = parseTime(draft); if (p) commit(p); else setDraft(value); setOpen(false); };
  const step = (dir: 1 | -1) => {
    const cur = parseTime(draft) ?? value;
    const next = dir > 0 ? Math.floor(toMin(cur) / STEP) + 1 : Math.ceil(toMin(cur) / STEP) - 1;
    commit(SLOTS[Math.min(SLOTS.length - 1, Math.max(0, next))]);
  };
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const k = e.key;
    if (k === "ArrowDown" || k === "ArrowUp") {
      e.preventDefault();
      const dir = k === "ArrowDown" ? 1 : -1;
      if (!open) { if (e.altKey) { setHi(nearest(parseTime(draft) ?? value)); setOpen(true); } else step(dir); return; }
      setHi((h) => { const n = Math.min(SLOTS.length - 1, Math.max(0, (h < 0 ? nearest(parseTime(draft) ?? value) : h) + dir)); list.current?.children[n]?.scrollIntoView({ block: "nearest" }); return n; });
    } else if (k === "Enter" && open && hi >= 0) { e.preventDefault(); pick(SLOTS[hi]); }
    else if (k === "Enter") { const p = parseTime(draft); if (p) commit(p); }
    else if (k === "Escape" && open) { e.preventDefault(); e.stopPropagation(); e.nativeEvent.stopImmediatePropagation(); setOpen(false); }
  };
  const onText = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = mask(e.target.value, draft);
    setDraft(v);
    if (/^\d{2}:\d{2}$/.test(v) && parseTime(v)) { if (v !== value) onChange(v); setHi(nearest(v)); }
  };
  const sel = parseTime(draft) ?? value;
  return (
    <div className={`tf${bad ? " bad" : ""}`}>
      <input ref={input} className="tf-in" inputMode="numeric" autoComplete="off" spellCheck={false} placeholder="ЧЧ:ММ" aria-label={label} role="combobox" aria-expanded={open} aria-haspopup="listbox" aria-invalid={bad}
             value={draft} onChange={onText} onKeyDown={onKey} onBlur={settle} onFocus={(e) => e.currentTarget.select()} />
      <button type="button" className="tf-btn" tabIndex={-1} aria-label={`${label}: выбрать из списка`} onMouseDown={(e) => e.preventDefault()}
              onClick={() => { if (open) setOpen(false); else { setHi(nearest(sel)); setOpen(true); input.current?.focus(); } }}><Icon name="clock" size={16} /></button>
      {open && pos && createPortal(
        <ul ref={list} className="tf-list" role="listbox" aria-label={label} style={pos} onMouseDown={(e) => e.preventDefault()}>
          {SLOTS.map((t, i) => <li key={t} role="option" aria-selected={t === sel} data-cur={t === sel ? "" : undefined} className={`tf-opt${i === hi ? " hi" : ""}`} onClick={() => pick(t)} onMouseEnter={() => setHi(i)}>{t}</li>)}
        </ul>, document.body)}
    </div>
  );
}
