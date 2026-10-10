import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../components/Icons";
import { fmtTime } from "../mediaPlayerMath";
import { currentIndex, searchCues, type Cue } from "./playerMath";

interface Props {
  cues: Cue[];
  time: number;
  follow: boolean;
  onFollow: (v: boolean) => void;
  onSeek: (t: number) => void;
  unavailable?: string | null;
  scope: "meeting" | "participant";
}

/**
 * Стенограмма рядом с записью: имена, время, подсветка текущей реплики, автопрокрутка (отключается, если пользователь читает сам; «К текущему моменту» возвращает),
 * переход к началу реплики по щелчку и поиск по тексту.
 */
export default function TranscriptPanel({ cues, time, follow, onFollow, onSeek, unavailable, scope }: Props) {
  const list = useRef<HTMLDivElement>(null);
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const cur = currentIndex(cues, time);
  const hits = useMemo(() => searchCues(cues, q), [cues, q]);
  const hitSet = useMemo(() => new Set(hits), [hits]);
  const scrolledByCode = useRef(false);

  const scrollTo = (i: number, smooth = true) => {
    const el = list.current?.querySelector<HTMLElement>(`[data-i="${i}"]`);
    if (!el) return;
    scrolledByCode.current = true;
    el.scrollIntoView({ block: "center", behavior: smooth ? "smooth" : "auto" });
    window.setTimeout(() => { scrolledByCode.current = false; }, 600);
  };
  useEffect(() => { if (follow && cur >= 0) scrollTo(cur); }, [cur, follow]);                                // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setAt(0); if (hits.length) { onFollow(false); scrollTo(hits[0]); } }, [hits]);          // eslint-disable-line react-hooks/exhaustive-deps

  const next = (d: 1 | -1) => {
    if (!hits.length) return;
    const n = (at + d + hits.length) % hits.length;
    setAt(n); onFollow(false); scrollTo(hits[n]);
  };
  // ручная прокрутка отключает слежение, чтобы текст не «убегал» из-под рук
  const manual = () => { if (!scrolledByCode.current && follow) onFollow(false); };

  return (
    <aside className="plr-tr" aria-label="Стенограмма">
      <div className="plr-tr-bar">
        <div className="plr-search"><Icon name="search" size={14} />
          <input type="search" value={q} placeholder="Поиск по тексту" aria-label="Поиск по стенограмме" onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); next(e.shiftKey ? -1 : 1); } e.stopPropagation(); }} />
          {q && <span className="muted small" aria-live="polite">{hits.length ? `${at + 1} из ${hits.length}` : "не найдено"}</span>}
        </div>
        <button type="button" className={`btn mini ${follow ? "primary" : ""}`} onClick={() => { onFollow(true); if (cur >= 0) scrollTo(cur); }}
                title="Прокручивать стенограмму за воспроизведением и вернуться к текущей реплике" aria-pressed={follow}>К текущему моменту</button>
      </div>
      {unavailable && <div className="muted small plr-tr-empty" role="status">{unavailable}</div>}
      <div className="plr-tr-list" ref={list} onWheel={manual} onTouchMove={manual}>
        {!unavailable && !cues.length && <div className="muted small plr-tr-empty">Реплик нет.</div>}
        {cues.map((c, i) => (
          <button type="button" key={c.id} data-i={i} className={`plr-cue ${i === cur ? "now" : ""} ${hitSet.has(i) ? "hit" : ""}`} onClick={() => onSeek(c.start)} title="Перейти к этой реплике">
            <span className="plr-cue-t">{fmtTime(c.start)}</span>
            <span className="plr-cue-b">{scope === "meeting" && <b className="plr-cue-s">{c.speaker}</b>}<span>{c.text}</span></span>
          </button>
        ))}
      </div>
    </aside>
  );
}
