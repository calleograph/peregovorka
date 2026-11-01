/** Период «с … по …» одной группой: подписи в строку с полями, поля одной ширины, «по» не раньше «с». */
export default function DateRange({ since, until, onSince, onUntil, label = "Период" }: {
  since: string; until: string; onSince: (v: string) => void; onUntil: (v: string) => void; label?: string;
}) {
  return (
    <div className="date-range" role="group" aria-label={label}>
      <label>с <input type="date" value={since} max={until || undefined} onChange={(e) => onSince(e.target.value)} aria-label={`${label}: с даты`} /></label>
      <label>по <input type="date" value={until} min={since || undefined} onChange={(e) => onUntil(e.target.value)} aria-label={`${label}: по дату`} /></label>
    </div>
  );
}
