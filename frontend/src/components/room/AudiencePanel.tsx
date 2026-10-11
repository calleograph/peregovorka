import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../Icons";
import { audienceCounts, audienceLabel, filterAudience, sortAudience, windowRange, type AudiencePerson } from "../../presentation";

export const AUD_ROW_H = 40;

interface RowProps<T extends AudiencePerson> {
  p: T;
  canManage: boolean;
  onGrant: (p: T) => void;
  onLowerHand: (p: T) => void;
  onCard: (p: T) => void;
}

/** Одна строка списка зрителей. `memo`: при изменении одного зрителя перерисовывается только его строка (объекты участников не пересоздаются без причины). */
function RowImpl<T extends AudiencePerson>({ p, canManage, onGrant, onLowerHand, onCard }: RowProps<T>) {
  return (
    <li className={`aud-row ${p.local ? "me" : ""}`} style={{ height: AUD_ROW_H }} data-identity={p.identity}>
      <button type="button" className="aud-name" onClick={() => onCard(p)} title={p.name}>
        {p.name}{p.local ? " (вы)" : ""}
      </button>
      {p.hand && <span className="aud-hand" title={`Поднята рука, очередь: ${p.handOrder ?? "?"}`}><Icon name="hand" size={14} /> {p.handOrder ?? ""}</span>}
      {canManage && !p.local && (
        <span className="aud-acts">
          {p.hand && <button type="button" className="btn mini ghost" onClick={() => onLowerHand(p)} title="Опустить руку">✕</button>}
          <button type="button" className="btn mini primary" onClick={() => onGrant(p)} title="Дать слово: микрофон, камера, показ экрана и правка доски станут доступны">Дать слово</button>
        </span>
      )}
    </li>
  );
}
const Row = memo(RowImpl) as typeof RowImpl;

/**
 * Компактный список зрителей презентации: число зрителей, поиск, поднятые руки сверху и «виртуализация» — рисуются только видимые строки
 * (с запасом), поэтому тысяча зрителей — это примерно два десятка элементов в DOM. Руководитель отсюда даёт слово.
 */
export default function AudiencePanel<T extends AudiencePerson>({ people, canManage, onGrant, onLowerHand, onCard, onClose }: {
  people: T[];
  canManage: boolean;
  onGrant: (p: T) => void;
  onLowerHand: (p: T) => void;
  onCard: (p: T) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(360);
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef({ onGrant, onLowerHand, onCard });
  cb.current = { onGrant, onLowerHand, onCard };
  const grant = useCallback((p: T) => cb.current.onGrant(p), []);
  const lower = useCallback((p: T) => cb.current.onLowerHand(p), []);
  const card = useCallback((p: T) => cb.current.onCard(p), []);

  useEffect(() => { const t = window.setTimeout(() => setDebounced(query), 150); return () => window.clearTimeout(t); }, [query]);
  const sorted = useMemo(() => sortAudience(people), [people]);
  const shown = useMemo(() => filterAudience(sorted, debounced), [sorted, debounced]);
  const counts = useMemo(() => audienceCounts(people), [people]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setViewH(el.clientHeight || 360);
    measure();
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, []);
  useEffect(() => { box.current?.scrollTo?.({ top: 0 }); setScrollTop(0); }, [debounced]);

  const w = windowRange(shown.length, AUD_ROW_H, scrollTop, viewH);
  const slice = shown.slice(w.start, w.end);
  return (
    <aside className="aud-panel" role="complementary" aria-label="Зрители презентации">
      <div className="aud-head">
        <b>{audienceLabel(counts.viewers)}</b>
        {counts.hands > 0 && <span className="aud-hand" title="Подняли руку"><Icon name="hand" size={14} /> {counts.hands}</span>}
        <span className="spacer" />
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Закрыть список зрителей"><Icon name="close" size={16} /></button>
      </div>
      <input className="aud-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Найти зрителя по имени" aria-label="Поиск по зрителям" autoComplete="off" />
      <div className="aud-list" ref={box} onScroll={(e) => setScrollTop((e.target as HTMLElement).scrollTop)} tabIndex={0} aria-label={`Список зрителей: ${shown.length}`}>
        {shown.length === 0
          ? <p className="muted small aud-empty">{debounced ? "Никого не нашли." : "Зрителей пока нет."}</p>
          : (
            <ul style={{ paddingTop: w.padTop, paddingBottom: w.padBottom }} data-rendered={slice.length} data-total={shown.length}>
              {slice.map((p) => <Row key={p.identity} p={p} canManage={canManage} onGrant={grant} onLowerHand={lower} onCard={card} />)}
            </ul>
          )}
      </div>
    </aside>
  );
}
