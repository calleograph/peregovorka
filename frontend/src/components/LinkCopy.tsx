import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { copyText } from "../util";
import { Icon } from "./Icons";

type Toast = (text: string, tone?: "ok" | "error") => void;
export const STAFF_TIP = "Копировать ссылку для сотрудников";
export const GUEST_TIP = "Копировать гостевую ссылку";

/** Подсказка кнопки: появляется при наведении и при фокусе с клавиатуры (Esc закрывает), рисуется в document.body — карточки её не обрезают. */
function Tip({ text, at, id }: { text: string; at: { x: number; y: number } | null; id: string }) {
  if (!at) return null;
  return createPortal(<div id={id} role="tooltip" className="lc-tip" style={{ left: at.x, top: at.y }}>{text}</div>, document.body);
}

function CopyButton({ kind, label, tip, onClick }: { kind: "staff" | "guest"; label: string; tip: string; onClick: () => Promise<boolean> }) {
  const ref = useRef<HTMLButtonElement>(null);
  const id = useId();
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (!at) return;
    const off = (e: Event) => { if (!(e instanceof KeyboardEvent) || e.key === "Escape") setAt(null); };
    window.addEventListener("scroll", off, true); window.addEventListener("resize", off); document.addEventListener("keydown", off);
    return () => { window.removeEventListener("scroll", off, true); window.removeEventListener("resize", off); document.removeEventListener("keydown", off); };
  }, [at]);
  const show = () => { const r = ref.current?.getBoundingClientRect(); if (r) setAt({ x: Math.round(Math.min(r.right, window.innerWidth - 8)), y: Math.round(r.bottom + 6) }); };
  return (
    <>
      <button ref={ref} type="button" className={`iconbtn lc lc-${kind}${state === "done" ? " done" : ""}`} aria-label={label} aria-describedby={at ? id : undefined} disabled={state === "busy"}
              onPointerEnter={show} onPointerLeave={() => setAt(null)} onFocus={(e) => { if (e.currentTarget.matches(":focus-visible")) show(); }} onBlur={() => setAt(null)}
              onClick={(e) => {
                e.stopPropagation();
                setState("busy");
                void onClick().then((ok) => { setState(ok ? "done" : "idle"); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setState("idle"), 1400); }).catch(() => setState("idle"));
              }}>
        <Icon name={state === "done" ? "check" : "copy"} size={16} />
      </button>
      <Tip text={tip} at={at} id={id} />
    </>
  );
}

export interface GuestSource {
  /** готовая гостевая ссылка; null — её ещё нет */
  url: string | null;
  /** если ссылки нет, но человек вправе её выпустить: возвращает адрес (сервер выпускает ровно одну ссылку и повторно отдаёт прежнюю) */
  issue?: () => Promise<string>;
}

/** Две кнопки быстрого копирования на карточке встречи или комнаты: зелёная — ссылка для сотрудников, красная — гостевая (только если гости разрешены и человек вправе ею делиться).
 *  Один компонент для плиток, строк и «Предстоящих встреч»; управление ссылками (отзыв, перевыпуск, срок) остаётся в меню «⋯». */
export function LinkCopy({ name, staffUrl, guest, onCopy }: { name: string; staffUrl: string | null; guest: GuestSource | null; onCopy: Toast }) {
  const [issued, setIssued] = useState<string | null>(null);
  const done = (ok: boolean, what: string) => { onCopy(ok ? `${what} скопирована` : "Не удалось скопировать — выделите ссылку вручную", ok ? "ok" : "error"); return ok; };
  const guestUrl = guest?.url ?? issued;
  return (
    <span className="lc-group">
      {staffUrl && <CopyButton kind="staff" tip={STAFF_TIP} label={`${STAFF_TIP}: ${name}`} onClick={async () => done(await copyText(staffUrl), "Ссылка для сотрудников")} />}
      {guest && (guestUrl || guest.issue) && (
        <CopyButton kind="guest" tip={GUEST_TIP} label={`${GUEST_TIP}: ${name}`} onClick={async () => {
          let url = guestUrl;
          if (!url && guest.issue) {
            try { url = await guest.issue(); setIssued(url); } catch (e) { onCopy((e as Error).message || "Не удалось подготовить гостевую ссылку", "error"); return false; }
          }
          return done(await copyText(url!), "Гостевая ссылка");
        }} />
      )}
    </span>
  );
}
