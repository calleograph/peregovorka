import { useEffect, useState, type CSSProperties } from "react";
import { FrameGovernor, sceneMode, wavePath, type SceneMode } from "../loginScenes";
import "../loginScenes.css";

/**
 * Фон страницы входа — одна композиция «волны и орбиты»: объёмный градиент, крупные полупрозрачные волны внизу, наклонные орбиты справа сверху,
 * большая дуга со световыми узлами слева, перспективная сетка под волнами справа, мягкий свет за формой входа и под подписью внизу.
 * Слои привязаны к краям окна (а не к общему холсту), поэтому на любом соотношении сторон композиция цельная и ничего не обрезается посередине.
 *
 * Движение — только transform и opacity отдельных слоёв (их считает видеокарта): волны медленно текут, свет «дышит», световые точки идут по орбитам,
 * орбиты чуть покачиваются, искры на гребнях появляются и гаснут. Цвета — из переменных темы (--accent, --surface, --bg), поэтому фон следует за темой.
 * Нагрузка: замер плавности короткими окнами — при медленных кадрах движение становится спокойнее, затем останавливается; во вкладке в фоне всё стоит;
 * «уменьшить движение» в системе, экономия трафика, слабый процессор — та же картинка без движения; телефон и планшет — упрощённая и неподвижная.
 */
export default function LoginScenes() {
  const [mode, setMode] = useState<SceneMode>(() => currentMode());
  const [level, setLevel] = useState(0);
  const [visible, setVisible] = useState(() => !document.hidden);

  useEffect(() => {
    const update = () => setMode(currentMode());
    const mqs = ["(prefers-reduced-motion: reduce)", "(hover: hover) and (pointer: fine)"].map((q) => window.matchMedia?.(q)).filter(Boolean) as MediaQueryList[];
    mqs.forEach((m) => m.addEventListener?.("change", update));
    window.addEventListener("resize", update);
    const vis = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", vis);
    return () => { mqs.forEach((m) => m.removeEventListener?.("change", update)); window.removeEventListener("resize", update); document.removeEventListener("visibilitychange", vis); };
  }, []);

  const moving = mode === "animated" && level < 2 && visible;
  // замер плавности: 2 с через 3 с после открытия, затем раз в 20 с; между окнами — ни одного лишнего кадра
  useEffect(() => {
    if (!moving) return;
    const gov = new FrameGovernor();
    gov.level = level;
    let raf = 0, last = 0, until = 0;
    const tick = (now: number) => {
      if (last) gov.push(now - last);
      last = now;
      if (now < until) raf = requestAnimationFrame(tick);
      else { last = 0; const lv = gov.settle(); if (lv !== level) setLevel(lv); }
    };
    const sample = () => { until = performance.now() + 2000; raf = requestAnimationFrame(tick); };
    const first = window.setTimeout(sample, 3000);
    const every = window.setInterval(sample, 20_000);
    return () => { window.clearTimeout(first); window.clearInterval(every); cancelAnimationFrame(raf); };
  }, [moving, level]);

  const still = mode !== "animated" || level >= 2;
  return (
    <div className={`lscn ${mode === "mobile" ? "mobile" : ""} lv-${level}${still ? " still" : " live"}${visible ? "" : " paused"}`} aria-hidden="true">
      <div className="lb-light l1" /><div className="lb-light l2" /><div className="lb-light l3" />
      {mode !== "mobile" && <Mesh />}
      <LeftArc />
      <Orbits />
      <Waves />
      <div className="lb-halo" />
    </div>
  );
}

function currentMode(): SceneMode {
  const mq = (q: string) => window.matchMedia?.(q).matches ?? false;
  return sceneMode({
    // сенсорное устройство — всё, у чего основной указатель не мышь/тачпад с наведением (телефон, планшет; ноутбук с сенсорным экраном остаётся компьютером)
    width: window.innerWidth, coarse: !mq("(hover: hover) and (pointer: fine)"), reduced: mq("(prefers-reduced-motion: reduce)"),
    saveData: (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData ?? false, cores: navigator.hardwareConcurrency,
  });
}

// ── волны: четыре слоя от дальнего к ближнему; у каждого — две одинаковые волны подряд (ширина 200 %), сдвиг на половину замыкает движение без шва ──
const W = 1200, H = 600;
const WAVES: { base: number; amp: [number, number]; phase: number; cls: string; lines?: boolean }[] = [
  { base: 250, amp: [46, 18], phase: 0.6, cls: "w0" },                  // дальняя, размытая
  { base: 330, amp: [38, 22], phase: 2.1, cls: "w1", lines: true },     // лента со световыми линиями по гребню
  { base: 410, amp: [30, 14], phase: 4.0, cls: "w2" },
  { base: 480, amp: [22, 10], phase: 5.2, cls: "w3" },                  // ближняя, самая светлая у края
];

function Waves() {
  return (
    <div className="lb-waves">
      {WAVES.map((w, i) => (
        <div key={i} className={`lb-wave ${w.cls}`}>
          <div className="lb-track">
            <svg viewBox={`0 0 ${2 * W} ${H}`} preserveAspectRatio="none" focusable="false">
              <defs>
                <linearGradient id={`lbw-${i}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" className="g-acc" style={{ stopOpacity: "var(--wave-top)" } as CSSProperties} />
                  <stop offset="0.55" className="g-acc" style={{ stopOpacity: "var(--wave-mid)" } as CSSProperties} />
                  <stop offset="1" className="g-acc" style={{ stopOpacity: "var(--wave-low)" } as CSSProperties} />
                </linearGradient>
                {i === 0 && <filter id="lbw-blur" x="-5%" y="-30%" width="110%" height="160%"><feGaussianBlur stdDeviation="10" /></filter>}
              </defs>
              <path d={`${wavePath(W, w.base, w.amp, w.phase, 2)} V${H} H0 Z`} fill={`url(#lbw-${i})`} filter={i === 0 ? "url(#lbw-blur)" : undefined} />
              <path d={wavePath(W, w.base, w.amp, w.phase, 2)} className="lb-crest" vectorEffect="non-scaling-stroke" />
              {w.lines && [14, 30, 50].map((dy, k) => <path key={k} d={wavePath(W, w.base + dy, [w.amp[0] * (1 - k * 0.12), w.amp[1]], w.phase + 0.18 * (k + 1), 2)} className={`lb-ray r${k}`} vectorEffect="non-scaling-stroke" />)}
            </svg>
          </div>
        </div>
      ))}
      {/* искры на гребнях: появляются и гаснут (в долях ширины и высоты слоя волн) */}
      {[[14, 47], [33, 36], [57, 44], [76, 30], [90, 41]].map(([x, y], i) => <i key={i} className={`lb-spark s${i}`} style={{ left: `${x}%`, top: `${y}%` }} />)}
    </div>
  );
}

// ── орбиты справа сверху: концентрические эллипсы и две световые точки, идущие по ним ──
const ORBITS = [{ rx: 470, ry: 186 }, { rx: 372, ry: 148 }, { rx: 280, ry: 112 }];

function Orbits() {
  return (
    <div className="lb-orbits">
      <div className="lb-sway">
        <svg viewBox="-500 -500 1000 1000" focusable="false">
          <defs>
            <radialGradient id="lbo-core"><stop offset="0" className="g-acc" style={{ stopOpacity: 0.32 }} /><stop offset="1" className="g-acc" style={{ stopOpacity: 0 }} /></radialGradient>
          </defs>
          <ellipse rx="300" ry="190" fill="url(#lbo-core)" />
          {ORBITS.map((o, i) => <ellipse key={i} rx={o.rx} ry={o.ry} className={`lb-orbit o${i}`} />)}
          <ellipse rx="430" ry="168" className="lb-orbit dash" />
          {[[-407, 93], [322, -74], [-96, -105]].map(([x, y], i) => <circle key={i} cx={x} cy={y} r="4" className="lb-node" />)}
        </svg>
        {ORBITS.slice(0, 2).map((o, i) => <Runner key={i} rx={o.rx / 10} ry={o.ry / 10} cls={`run${i}`} />)}
        {[[9.3, 59.3], [82.2, 42.6], [40.4, 39.5]].map(([x, y], i) => <i key={i} className={`lb-halo-node h${i}`} style={{ left: `${x}%`, top: `${y}%` }} />)}
      </div>
    </div>
  );
}

/**
 * Световая точка на орбите: маленький элемент идёт по эллипсу (offset-path: ellipse в долях слоя орбит — совпадает с нарисованной орбитой при любом размере окна);
 * за ней — три точки поменьше с отставанием (хвост). Двигается только сама точка размером в несколько пикселей: без видеокарты это почти ничего не стоит,
 * в отличие от вращения большого прозрачного слоя. Браузер без offset-path оставляет точку на месте — картинка та же, без движения.
 */
function Runner({ rx, ry, cls }: { rx: number; ry: number; cls: string }) {
  const path = { offsetPath: `ellipse(${rx}% ${ry}% at 50% 50%)` } as CSSProperties;
  return (
    <div className={`lb-run ${cls}`}>
      {[3, 2, 1].map((k) => <i key={k} className={`lb-dot tail t${k}`} style={path} />)}
      <i className="lb-dot" style={path} />
    </div>
  );
}

// ── большая дуга слева: часть окружности, центр за левым краем; по ней идёт световая точка, на ней — два узла ──
function LeftArc() {
  return (
    <div className="lb-arc">
      <svg viewBox="-500 -500 1000 1000" focusable="false">
        <circle r="440" className="lb-orbit o0" />
        <circle r="372" className="lb-orbit dash" />
        <circle cx="404" cy="-174" r="4" className="lb-node" />
        <circle cx="300" cy="322" r="4" className="lb-node" />
        <path d="M404 -174 C470 -60 470 60 430 120" className="lb-link" />
      </svg>
      <Runner rx={44} ry={44} cls="run2" />
      <i className="lb-halo-node h3" style={{ left: "90.4%", top: "32.6%" }} />
      <i className="lb-halo-node h4" style={{ left: "80%", top: "82.2%" }} />
    </div>
  );
}

// ── перспективная сетка справа снизу, под волнами: «поверхность», растворяется к краям ──
function Mesh() {
  const rows = Array.from({ length: 9 }, (_, i) => 400 * Math.pow(i / 8, 1.7));
  const cols = Array.from({ length: 17 }, (_, i) => -400 + i * 100);
  return (
    <div className="lb-mesh">
      <svg viewBox="0 0 800 400" preserveAspectRatio="none" focusable="false">
        {rows.map((y, i) => <line key={`h${i}`} x1="0" x2="800" y1={y} y2={y} />)}
        {cols.map((x, i) => <line key={`v${i}`} x1={400 + (x - 400) * 0.18} y1="0" x2={x} y2="400" />)}
      </svg>
    </div>
  );
}
