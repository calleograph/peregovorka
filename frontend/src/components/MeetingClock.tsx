import { useEffect, useState } from "react";

const MSK = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });

/** Длительность «Ч:ММ:СС» (меньше часа — «ММ:СС»); отрицательное — от расхождения часов компьютера и сервера — как ноль. */
export function elapsedText(ms: number): string {
  const t = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const p = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}

export const mskText = (d: Date): string => MSK.format(d);

/** Неброские часы в шапке комнаты: сколько идёт встреча и время по Москве. */
export default function MeetingClock({ startedAt }: { startedAt?: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);
  const start = startedAt ? Date.parse(startedAt) : NaN;
  const msk = mskText(new Date(now));
  return (
    <span className="meet-clock" title={`${Number.isNaN(start) ? "" : `Встреча идёт ${elapsedText(now - start)}. `}Сейчас ${msk} по Москве`}>
      {!Number.isNaN(start) && <span className="meet-clock-el">{elapsedText(now - start)}</span>}
      <span className="meet-clock-msk">{msk}&nbsp;МСК</span>
    </span>
  );
}
