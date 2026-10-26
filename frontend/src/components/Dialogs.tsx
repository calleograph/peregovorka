import { useEffect, useRef, useState, type ReactNode } from "react";
import { focusInitial } from "../initialFocus";

/** Модальное окно: Escape и клик по фону закрывают, фокус при открытии — в первое поле ввода (`focusInitial`: правила в initialFocus.ts), при закрытии — возвращается на кнопку, открывшую окно. */
export function Modal({ title, onClose, children, wide, noAutofocus }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; /** Окно для чтения или выбора действия: курсор в поле ввода не ставится (см. initialFocus.ts). */ noAutofocus?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;                 // обработчик может меняться при каждой перерисовке родителя; эффект ниже выполняется один раз и фокус при фоновом обновлении не трогает
  const opener = useRef<HTMLElement | null>(null);
  if (opener.current === null) opener.current = document.activeElement as HTMLElement | null;      // до монтирования дочерних полей: поле с autoFocus фокус уже заберёт
  useEffect(() => {
    const prev = opener.current;
    if (ref.current) focusInitial(ref.current);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") close.current(); };
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("keydown", esc); if (prev && prev.isConnected) prev.focus?.(); };
  }, []);
  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref} tabIndex={-1} data-no-autofocus={noAutofocus ? "" : undefined} style={wide ? { width: "min(980px, 100%)" } : undefined}>
        <div className="row"><h2>{title}</h2><div className="spacer" /><button className="btn ghost mini" onClick={onClose} aria-label="Закрыть">✕</button></div>
        {children}
      </div>
    </div>
  );
}

/**
 * Подтверждение необратимого действия. Если задан `typed`, нужно ввести это слово — для самых разрушительных операций
 * (удаление встречи со всеми материалами) случайный клик не сработает.
 */
export function ConfirmDialog({ title, body, confirmLabel, typed, danger = true, onConfirm, onClose }: {
  title: string; body: ReactNode; confirmLabel: string; typed?: string; danger?: boolean;
  onConfirm: () => Promise<void> | void; onClose: () => void;
}) {
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const go = async () => {
    setBusy(true); setErr("");
    try { await onConfirm(); onClose(); } catch (e) { setErr((e as Error).message || "Не удалось выполнить действие"); setBusy(false); }
  };
  return (
    <Modal title={title} onClose={busy ? () => undefined : onClose}>
      <div>{body}</div>
      {typed && (
        <label>Для подтверждения введите «{typed}»
          <input value={word} onChange={(e) => setWord(e.target.value)} autoFocus autoComplete="off" />
        </label>
      )}
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="row">
        <button className={`btn ${danger ? "danger" : "primary"}`} onClick={go} disabled={busy || (typed !== undefined && word.trim().toLowerCase() !== typed.toLowerCase())}>{busy ? "Выполняется…" : confirmLabel}</button>
        <button className="btn ghost" onClick={onClose} disabled={busy}>Отмена</button>
      </div>
    </Modal>
  );
}
