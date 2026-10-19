import type { Signal } from "../quality/signal";

/** Четыре столбика уровня сигнала; цвет — зелёный/жёлтый/красный. Подробности (потери, задержка, причина ограничения) — во всплывающей подсказке. */
export default function SignalBars({ signal, title }: { signal: Signal; title: string }) {
  return (
    <span className={`sig sig-${signal.tone}`} role="img" aria-label={`Качество связи: ${signal.label}`} title={title}>
      <svg width="18" height="16" viewBox="0 0 18 16" aria-hidden>
        {[0, 1, 2, 3].map((i) => <rect key={i} x={i * 4.6} y={14 - (i + 1) * 3.4} width="3.2" height={(i + 1) * 3.4} rx="0.8" className={i < signal.bars ? "on" : "off"} />)}
      </svg>
    </span>
  );
}
