import { SLOW_STAGE_SECONDS, STAGES, STAGE_HINT, STAGE_LABEL, type Stage } from "../../diagnostics";

interface Props {
  stage: Stage;
  /** Сколько мс длится текущий этап. */
  elapsedMs: number;
  /** Длительность уже завершённых этапов, мс. */
  done: Partial<Record<Stage, number>>;
  /** Дополнительная строка (например, «повторное подключение, попытка 2»). */
  note?: string;
}

const sec = (ms: number) => (ms / 1000).toFixed(ms < 10000 ? 1 : 0);

/** Этапы входа вместо вечного «Подключение…»: видно, на каком шаге находится вход и сколько он уже длится. */
export default function ConnectProgress({ stage, elapsedMs, done, note }: Props) {
  const idx = STAGES.indexOf(stage);
  const slow = stage !== "ready" && elapsedMs >= SLOW_STAGE_SECONDS * 1000;
  return (
    <div className="progress card" role="status" aria-live="polite">
      <h2>Вход в комнату</h2>
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
      {slow && (
        <div className="alert" role="alert">
          Этап «{STAGE_LABEL[stage]}» занимает дольше обычного ({sec(elapsedMs)} с). {STAGE_HINT[stage]}
        </div>
      )}
    </div>
  );
}
