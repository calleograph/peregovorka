import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../components/Icons";
import { fmtTime } from "../mediaPlayerMath";
import { copyText } from "../util";
import { countMatches, currentIndex, matchRanges, quoteOf, searchCues, type Cue } from "./playerMath";

interface Props {
  cues: Cue[];
  time: number;
  follow: boolean;
  onFollow: (v: boolean) => void;
  onSeek: (t: number) => void;
  onPlay: (t: number) => void;
  momentUrl: (t: number) => string;
  unavailable?: string | null;
  scope: "meeting" | "participant";
}

function Marked({ text, q }: { text: string; q: string }) {
  const ranges = matchRanges(text, q);
  if (!ranges.length) return <>{text}</>;
  const out: React.ReactNode[] = []; let pos = 0;
  ranges.forEach(([a, b], i) => { out.push(<Fragment key={`t${i}`}>{text.slice(pos, a)}</Fragment>, <mark key={`m${i}`}>{text.slice(a, b)}</mark>); pos = b; });
  out.push(<Fragment key="end">{text.slice(pos)}</Fragment>);
  return <>{out}</>;
}

/**
 * Стенограмма рядом с записью: имена, время, подсветка текущей реплики, автопрокрутка (отключается, если пользователь читает сам; «К текущему моменту» возвращает),
 * поиск слова или фразы (подсветка, число совпадений, переходы, фильтр по участнику), перемотка записи к найденному, прослушивание реплики, копирование цитаты и ссылки на момент.
 * Время реплик — из распознавания (начало сегмента), без ложной точности внутри него.
 */
export default function TranscriptPanel({ cues, time, follow, onFollow, onSeek, onPlay, momentUrl, unavailable, scope }: Props) {
  const list = useRef<HTMLDivElement>(null);
  const [q, setQ] = useState("");
  const [who, setWho] = useState("");
  const [at, setAt] = useState(0);
  const [note, setNote] = useState("");
  const cur = currentIndex(cues, time);
  const speakers = useMemo(() => [...new Set(cues.map((c) => c.speaker).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")), [cues]);
  const hits = useMemo(() => searchCues(cues, q, who), [cues, q, who]);
  const hitSet = useMemo(() => new Set(hits), [hits]);
  const total = useMemo(() => countMatches(cues, hits, q), [cues, hits, q]);
  const scrolledByCode = useRef(false);

  const scrollTo = (i: number, smooth = true) => {
    const el = list.current?.querySelector<HTMLElement>(`[data-i="${i}"]`);
    if (!el) return;
    scrolledByCode.current = true;
    el.scrollIntoView({ block: "center", behavior: smooth ? "smooth" : "auto" });
    window.setTimeout(() => { scrolledByCode.current = false; }, 600);
  };
  useEffect(() => { if (follow && cur >= 0) scrollTo(cur); }, [cur, follow]);                                // eslint-disable-line react-hooks/exhaustive-deps
  // новый поиск: первое совпадение показано, запись перемотана к нему
  useEffect(() => { setAt(0); if (hits.length) { onFollow(false); scrollTo(hits[0]); onSeek(cues[hits[0]].start); } }, [hits]);          // eslint-disable-line react-hooks/exhaustive-deps

  const go = (n: number) => { setAt(n); onFollow(false); scrollTo(hits[n]); onSeek(cues[hits[n]].start); };
  const next = (d: 1 | -1) => { if (hits.length) go((at + d + hits.length) % hits.length); };
  // ручная прокрутка отключает слежение, чтобы текст не «убегал» из-под рук
  const manual = () => { if (!scrolledByCode.current && follow) onFollow(false); };
  const say = (t: string) => { setNote(t); window.setTimeout(() => setNote(""), 2500); };
  const copy = async (text: string, ok: string) => say((await copyText(text)) ? ok : "Не удалось скопировать — выделите текст вручную");

  return (
    <aside className="plr-tr" aria-label="Стенограмма">
      <div className="plr-tr-bar">
        <div className="plr-search"><Icon name="search" size={14} />
          <input type="search" value={q} placeholder="Слово или фраза" aria-label="Поиск по стенограмме" onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); next(e.shiftKey ? -1 : 1); } e.stopPropagation(); }} />
        </div>
        {speakers.length > 1 && scope === "meeting" && (
          <select value={who} onChange={(e) => setWho(e.target.value)} aria-label="Участник" title="Искать только у выбранного участника">
            <option value="">Все участники</option>{speakers.map((s) => <option key={s} value={s}>{s}</option>)}</select>)}
        <button type="button" className={`btn mini ${follow ? "primary" : ""}`} onClick={() => { onFollow(true); if (cur >= 0) scrollTo(cur); }}
                title="Прокручивать стенограмму за воспроизведением и вернуться к текущей реплике" aria-pressed={follow}>К текущему моменту</button>
      </div>
      {(q.trim() || who) && (
        <div className="plr-found" role="status" aria-live="polite">
          <button type="button" className="icon-btn" onClick={() => next(-1)} disabled={!hits.length} aria-label="Предыдущее совпадение" title="Предыдущее (Shift+Enter)"><Icon name="chevronL" size={14} /></button>
          <span className="small">{hits.length ? `${at + 1} из ${hits.length}${q.trim() ? ` · совпадений: ${total}` : ""}` : "не найдено"}</span>
          <button type="button" className="icon-btn" onClick={() => next(1)} disabled={!hits.length} aria-label="Следующее совпадение" title="Следующее (Enter)"><Icon name="chevronR" size={14} /></button>
          {hits.length > 0 && <button type="button" className="btn mini" onClick={() => onPlay(cues[hits[at]].start)} title="Прослушать найденную реплику с начала">▶ Прослушать</button>}
        </div>)}
      {note && <div className="plr-note small" role="status">{note}</div>}
      {unavailable && <div className="muted small plr-tr-empty" role="status">{unavailable}</div>}
      <div className="plr-tr-list" ref={list} onWheel={manual} onTouchMove={manual}>
        {!unavailable && !cues.length && <div className="muted small plr-tr-empty">Реплик нет.</div>}
        {cues.map((c, i) => (
          <div key={c.id} data-i={i} className={`plr-cue ${i === cur ? "now" : ""} ${hitSet.has(i) ? "hit" : ""} ${hits[at] === i && hitSet.has(i) ? "hit-cur" : ""}`}>
            <button type="button" className="plr-cue-main" onClick={() => onSeek(c.start)} onDoubleClick={() => onPlay(c.start)} title="Перейти к этой реплике (двойной щелчок — прослушать)">
              <span className="plr-cue-t">{fmtTime(c.start)}</span>
              <span className="plr-cue-b">{scope === "meeting" && <b className="plr-cue-s">{c.speaker}</b>}<span><Marked text={c.text} q={q} /></span></span>
            </button>
            <span className="plr-cue-acts">
              <button type="button" className="icon-btn" onClick={() => onPlay(c.start)} aria-label="Прослушать с этого места" title="Прослушать с этого места"><Icon name="play" size={13} /></button>
              <button type="button" className="icon-btn" onClick={() => void copy(quoteOf(c, fmtTime, scope === "meeting"), "Цитата скопирована")} aria-label="Копировать цитату" title="Копировать цитату с именем и временем"><Icon name="copy" size={13} /></button>
              <button type="button" className="icon-btn" onClick={() => void copy(momentUrl(c.start), "Ссылка на момент скопирована")} aria-label="Копировать ссылку на момент" title="Ссылка на этот момент записи (откроется у тех, у кого есть доступ к встрече)">🔗</button>
            </span>
          </div>
        ))}
      </div>
    </aside>
  );
}
