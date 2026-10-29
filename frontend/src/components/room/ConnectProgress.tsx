import { useEffect, useId, useRef, useState } from "react";
import { SLOW_STAGE_SECONDS, STAGES, STAGE_HINT, STAGE_LABEL, type Stage } from "../../diagnostics";

interface Props {
  stage: Stage;
  /** Сколько мс длится текущий этап. */
  elapsedMs: number;
  /** Длительность уже завершённых этапов, мс. */
  done: Partial<Record<Stage, number>>;
  /** Дополнительная строка (например, «повторное подключение, попытка 2»). */
  note?: string;
  /** Связь потеряна уже после входа: вместо этапов входа — этот короткий статус, а `text` и `onRetry` — в подробностях. */
  lost?: { label: string; text: string };
  onRetry?: () => void;
}

const sec = (ms: number) => (ms / 1000).toFixed(ms < 10000 ? 1 : 0);
const SHORT: Record<Stage, string> = { prepare: "подготовка", server: "сигнал", media: "звук и видео", ready: "готово" };

/** Небольшой плавающий индикатор подключения: не занимает место в раскладке, поэтому сцена и панель управления не сдвигаются.
 *  Коротко — что сейчас происходит; по нажатию — этапы входа (или причина и кнопка «Войти снова» при потере связи). Родитель убирает индикатор, когда связь есть. */
export default function ConnectProgress({ stage, elapsedMs, done, note, lost, onRetry }: Props) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const popId = useId();
  const idx = STAGES.indexOf(stage);
  const slow = !lost && stage !== "ready" && elapsedMs >= SLOW_STAGE_SECONDS * 1000;

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("pointerdown", away, true); window.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="conn-chip" ref={box}>
      <button type="button" className={`conn-pill ${lost ? "lost" : slow ? "slow" : ""}`} aria-expanded={open} aria-controls={popId} onClick={() => setOpen((o) => !o)}
              title="Подробности подключения">
        <span className="conn-dots" aria-hidden>
          {lost ? <i className="warn" /> : STAGES.slice(0, 3).map((s, i) => <i key={s} className={i < idx ? "done" : i === idx ? "active" : ""} />)}
        </span>
        <span role="status" aria-live="polite" className="conn-text">
          {lost ? lost.label : `Подключение · ${SHORT[stage]}`}
          {!lost && stage !== "ready" && <span className="muted"> {sec(elapsedMs)} с</span>}
        </span>
      </button>
      {open && (
        <div className="conn-pop" id={popId} role="group" aria-label="Подробности подключения">
          {lost ? (
            <>
              <p className="small">{lost.text}</p>
              {onRetry && <button type="button" className="btn mini primary" onClick={() => { setOpen(false); onRetry(); }}>Войти снова</button>}
            </>
          ) : (
            <>
              {note && <p className="muted small">{note}</p>}
              <ol className="steps">
                {STAGES.map((s, i) => {
                  const state = i < idx || stage === "ready" ? "done" : i === idx ? "active" : "todo";
                  return (
                    <li key={s} className={`step ${state}`}>
                      <span className="mark" aria-hidden>{state === "done" ? "✓" : state === "active" ? "…" : "○"}</span>
                      <span className="label">{STAGE_LABEL[s]}</span>
                      <span className="muted small">
                        {state === "done" && done[s] !== undefined && `${sec(done[s]!)} с`}
                        {state === "active" && s !== "ready" && `${sec(elapsedMs)} с`}
                      </span>
                    </li>
                  );
                })}
              </ol>
              {slow && <p className="small conn-slow">Этап «{STAGE_LABEL[stage]}» занимает дольше обычного ({sec(elapsedMs)} с). {STAGE_HINT[stage]}</p>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
