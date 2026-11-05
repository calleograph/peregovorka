import type { ReactNode } from "react";
import InfoTip from "../components/InfoTip";

export type Level = -1 | 0 | 1 | 2;
export const lvClass = (lv: Level) => (lv < 0 ? "na" : lv === 2 ? "crit" : lv === 1 ? "warn" : "ok");
const nf = (d: number) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: d });
export const num = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : nf(d).format(v));
export const bps = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v >= 1e9 ? `${num(v / 1e9, 2)} Гбит/с` : v >= 1e6 ? `${num(v / 1e6, 1)} Мбит/с` : `${num(v / 1e3, 0)} кбит/с`);
export const kbps = (v: number | null | undefined) => (v === null || v === undefined ? "—" : bps(v * 1000));
export const bytes = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v >= 1024 ** 3 ? `${num(v / 1024 ** 3, 1)} ГБ` : v >= 1024 ** 2 ? `${num(v / 1024 ** 2, 0)} МБ` : `${num(v / 1024, 0)} КБ`);
export const level = (v: number | null | undefined, warn: number, crit: number): Level => (v === null || v === undefined ? -1 : v >= crit ? 2 : v >= warn ? 1 : 0);

export const Tip = ({ children }: { children: ReactNode }) => <InfoTip label="Что это значит">{children}</InfoTip>;

/** Круговой индикатор. Нет значения — пунктирное кольцо и прочерк: «нет данных» не выглядит как ноль. */
export function Ring({ pct, text, label, lv, tip, sub }: { pct: number | null | undefined; text: string; label: string; lv: Level; tip: ReactNode; sub?: string }) {
  const r = 21, c = 2 * Math.PI * r, p = pct === null || pct === undefined ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className={`ld-ring ${lvClass(lv)}`}>
      <svg viewBox="0 0 52 52" role="img" aria-label={`${label}: ${text}`}>
        <circle className="bg" cx="26" cy="26" r={r} />
        {pct !== null && pct !== undefined && <circle className="fg" cx="26" cy="26" r={r} strokeDasharray={`${(p / 100) * c} ${c}`} transform="rotate(-90 26 26)" />}
        <text x="26" y="26" dominantBaseline="central" textAnchor="middle" className={text.length > 4 ? "long" : undefined}>{text}</text>
      </svg>
      <span className="ld-ring-l">{label}<Tip>{tip}</Tip></span>
      {sub && <span className="ld-ring-s">{sub}</span>}
    </div>
  );
}

/** Малый график: разрывы в данных остаются разрывами, а не соединяются линией. */
export function Spark({ data, label, value, lv = 0, max, tip }: { data: (number | null | undefined)[]; label: string; value: string; lv?: Level; max?: number; tip?: ReactNode }) {
  const W = 120, H = 28, vals = data.filter((x): x is number => typeof x === "number");
  const top = Math.max(max ?? 0, ...vals, 1e-9), n = Math.max(data.length - 1, 1);
  const segs: string[] = [];
  let cur = "";
  data.forEach((v, i) => {
    if (typeof v !== "number") { if (cur) segs.push(cur); cur = ""; return; }
    cur += `${cur ? "L" : "M"}${(i / n * W).toFixed(1)} ${(H - 1 - (v / top) * (H - 3)).toFixed(1)}`;
  });
  if (cur) segs.push(cur);
  return (
    <div className={`ld-spark ${lvClass(lv)}`}>
      <div className="ld-spark-h"><span>{label}{tip && <Tip>{tip}</Tip>}</span><b>{value}</b></div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${label}: график`}>{segs.map((d, i) => <path key={i} d={d} vectorEffect="non-scaling-stroke" />)}</svg>
    </div>
  );
}

/** Строка «название — значение»: без значения — «нет данных» и пояснение почему. */
export function Row({ k, v, tip, na }: { k: string; v?: ReactNode; tip?: ReactNode; na?: string }) {
  const empty = v === null || v === undefined || v === "—";
  return <div className="ld-row"><span>{k}{tip && <Tip>{tip}</Tip>}</span><b className={empty ? "na" : ""}>{empty ? <>нет данных{na && <Tip>{na}</Tip>}</> : v}</b></div>;
}

export const Dot = ({ lv }: { lv: Level }) => <i className={`ld-dot ${lvClass(lv)}`} aria-hidden />;
