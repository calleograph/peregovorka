import { useEffect, useMemo, useState } from "react";
import { NET_LINKS, NET_NODES, linkPath, wantsMotion, wantsNetwork } from "../glassMath";

/**
 * Фон страницы входа: «встреча на связи». По краям окна — полупрозрачные стеклянные плитки участников (силуэт или «камера выключена»), между ними тонкие линии связи,
 * по которым медленно бегут световые импульсы — передача звука и видео. Лёгкий SVG + CSS: без WebGL и без кадров на JavaScript, ничего не слушает (ни курсор, ни ввод),
 * не перехватывает события (`pointer-events: none`) и скрыт от вспомогательных технологий. Анимация стоит, пока вкладка в фоне; при «уменьшить движение», экономии трафика
 * и на слабых устройствах остаётся статичная композиция; на узком телефоне не показывается. Цвета берутся из темы (--accent, --surface, --line).
 */
export default function LoginNetwork() {
  const [show] = useState(() => wantsNetwork(window.innerWidth));
  const [motion] = useState(() => wantsMotion({
    reduced: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    cores: navigator.hardwareConcurrency,
    saveData: (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData ?? false,
  }));
  const [visible, setVisible] = useState(() => !document.hidden);
  useEffect(() => {
    const on = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  const links = useMemo(() => NET_LINKS.map(([a, b], i) => {
    const na = NET_NODES.find((n) => n.id === a)!, nb = NET_NODES.find((n) => n.id === b)!;
    return { id: `${a}${b}`, d: linkPath(na, nb), delay: (i * 1.7) % 9, dur: 7 + (i % 4) * 1.5 };
  }), []);
  if (!show) return null;
  return (
    <svg className={`login-net${motion ? " moving" : ""}${visible ? "" : " paused"}`} viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
      <g className="ln-links">
        {links.map((l) => <path key={l.id} d={l.d} className="ln-line" />)}
        {motion && links.map((l) => <circle key={`p${l.id}`} r="3.4" className="ln-pulse" style={{ offsetPath: `path("${l.d}")`, animationDelay: `-${l.delay}s`, animationDuration: `${l.dur}s` }} />)}
      </g>
      {NET_NODES.map((n) => (
        <g key={n.id} transform={`translate(${n.x} ${n.y})`}><g className="ln-node" style={{ animationDelay: `-${n.delay}s`, animationDuration: `${n.dur}s` }}>
          <rect className="ln-tile" width={n.w} height={n.h} rx="16" />
          {n.cam
            ? <><circle className="ln-head" cx={n.w / 2} cy={n.h * 0.4} r={n.h * 0.16} /><path className="ln-body" d={`M${n.w * 0.28} ${n.h * 0.9} Q${n.w / 2} ${n.h * 0.46} ${n.w * 0.72} ${n.h * 0.9} Z`} /></>
            : <><circle className="ln-ava" cx={n.w / 2} cy={n.h / 2} r={n.h * 0.22} /><circle className="ln-dot" cx={n.w - 14} cy="14" r="4" /></>}
        </g></g>
      ))}
    </svg>
  );
}
