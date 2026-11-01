import { useEffect, useState, type ReactNode } from "react";
import { FADE_MS, FrameGovernor, SCENES, SCENE_MS, sceneMode, type SceneMode } from "../loginScenes";
import "../loginScenes.css";

/**
 * Фон страницы входа. На компьютере — четыре сцены, сменяющие друг друга каждые 10 с плавным перетеканием: «корпоративная сеть», «совместная работа»,
 * «волны и орбиты», «схема экосистемы»; внутри сцены — мягкое движение (только transform и opacity — их считает видеокарта, интерфейс не тормозит).
 * На телефоне и планшете — упрощённая статичная «волна». Цвета — из переменных темы (--accent, --surface, --text), поэтому фон следует за темой и
 * плавно перекрашивается при её смене. Середина, где стоит форма входа, свободна. Слой не перехватывает события и скрыт от вспомогательных технологий.
 * Нагрузка: замер плавности короткими окнами — при медленных кадрах движение становится спокойнее, затем останавливается; во вкладке в фоне всё стоит;
 * «уменьшить движение» в системе — статичная картинка.
 */
export default function LoginScenes() {
  const [mode, setMode] = useState<SceneMode>(() => currentMode());
  const [scene, setScene] = useState(0);
  const [prev, setPrev] = useState(-1);
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
  // смена сцен: прежняя гаснет, новая проявляется (переход — в CSS); во время перехода живут обе, остальные стоят
  useEffect(() => {
    if (!moving) return;
    const t = window.setInterval(() => setScene((s) => { setPrev(s); return (s + 1) % SCENES; }), SCENE_MS);
    return () => window.clearInterval(t);
  }, [moving]);
  useEffect(() => { if (prev < 0) return; const t = window.setTimeout(() => setPrev(-1), FADE_MS + 200); return () => window.clearTimeout(t); }, [prev]);

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

  if (mode === "mobile") return <div className="lscn mobile" aria-hidden="true"><MobileWaves /></div>;
  const still = mode !== "animated" || level >= 2;
  return (
    <div className={`lscn lv-${level}${still ? " still" : ""}${visible ? "" : " paused"}`} aria-hidden="true">
      {/* «уменьшить движение» и слабое устройство — спокойная «волна»; остановка из-за медленных кадров — та сцена, что была на экране (без скачка) */}
      {(still ? [mode === "animated" ? scene : 2] : [0, 1, 2, 3]).map((i) => {
        const on = still ? true : i === scene;
        const live = !still && (i === scene || i === prev);
        return <Scene key={i} index={i} on={on} live={live} />;
      })}
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

function Scene({ index, on, live }: { index: number; on: boolean; live: boolean }) {
  const [art, sprites] = [NETWORK, TEAM, WAVES, SCHEMA][index];
  return (
    <div className={`ls-scene ls-${index + 1}${on ? " on" : ""}${live ? " live" : ""}`}>
      <div className="ls-box">
        <svg className="ls-art" viewBox="0 0 1600 1000" focusable="false">{art()}</svg>
        {sprites()}
      </div>
    </div>
  );
}

/**
 * Движущаяся деталь — отдельный маленький SVG со своим слоем: его transform и opacity меняет видеокарта, без перерисовки всей сцены
 * (движение внутри одного большого SVG заставляло бы браузер заново растрировать весь фон в каждом кадре). Координаты — в точках холста 1600×1000.
 */
function Sprite({ x, y, w, h, cls = "", children }: { x: number; y: number; w: number; h: number; cls?: string; children: ReactNode }) {
  return (
    <svg className={`ls-sprite ${cls}`} viewBox={`0 0 ${w} ${h}`} overflow="visible" focusable="false"
         style={{ left: `${x / 16}%`, top: `${y / 10}%`, width: `${w / 16}%`, height: `${h / 10}%` }}>{children}</svg>
  );
}

/** Световое пятно, которое медленно «дышит» и смещается (CSS-градиент на своём слое). */
function Light({ x, y, r, cls = "" }: { x: number; y: number; r: number; cls?: string }) {
  return <div className={`ls-light ${cls}`} style={{ left: `${(x - r) / 16}%`, top: `${(y - r) / 10}%`, width: `${(2 * r) / 16}%`, height: `${(2 * r) / 10}%` }} />;
}

// ── общие элементы ────────────────────────────────────────────────────────────────────────

/** Неподвижное световое пятно в рисунке сцены (радиальный градиент). */
function Glow({ id, cx, cy, r, k = 1 }: { id: string; cx: number; cy: number; r: number; k?: number }) {
  return (
    <>
      <defs>
        <radialGradient id={id}>
          <stop offset="0" className="g-stop" style={{ stopOpacity: 0.3 * k }} />
          <stop offset="1" className="g-stop" style={{ stopOpacity: 0 }} />
        </radialGradient>
      </defs>
      <circle cx={cx} cy={cy} r={r} fill={`url(#${id})`} />
    </>
  );
}

type IconKind = "calendar" | "camera" | "group" | "chart" | "list" | "chat";
function Icon({ kind }: { kind: IconKind }): ReactNode {
  switch (kind) {
    case "calendar": return <g className="k-icon"><rect x="-16" y="-13" width="32" height="28" rx="5" /><path d="M-16 -4h32M-8 -18v8M8 -18v8" /><path d="M-8 4h4M2 4h4M-8 10h4" /></g>;
    case "camera": return <g className="k-icon"><rect x="-18" y="-11" width="25" height="22" rx="5" /><path d="M7 -3l11 -7v20l-11 -7z" /></g>;
    case "group": return <g className="k-icon"><circle cx="0" cy="-6" r="6" /><circle cx="-14" cy="-3" r="4.5" /><circle cx="14" cy="-3" r="4.5" /><path d="M-10 13q10 -14 20 0M-24 12q8 -11 16 -4M24 12q-8 -11 -16 -4" /></g>;
    case "chart": return <g className="k-icon"><path d="M-16 14h32" /><rect x="-13" y="2" width="6" height="10" rx="1.5" /><rect x="-3" y="-5" width="6" height="17" rx="1.5" /><rect x="7" y="-12" width="6" height="24" rx="1.5" /></g>;
    case "list": return <g className="k-icon"><path d="M-15 -9l3 3 5 -6M-15 2l3 3 5 -6M-15 13l3 3 5 -6" /><path d="M-2 -8h18M-2 3h18M-2 14h12" /></g>;
    case "chat": return <g className="k-icon"><path d="M-17 -12h34v20h-20l-8 7v-7h-6z" /><circle cx="-7" cy="-2" r="1.6" className="k-ink-fill" /><circle cx="0" cy="-2" r="1.6" className="k-ink-fill" /><circle cx="7" cy="-2" r="1.6" className="k-ink-fill" /></g>;
  }
}

/** Стеклянная карточка-профиль: аватар и две строки «текста». */
function ProfileCard({ x, y, w = 190, h = 74, cls = "" }: { x: number; y: number; w?: number; h?: number; cls?: string }) {
  return (
    <Sprite x={x} y={y} w={w} h={h} cls={`ls-float ${cls}`}>
      <rect className="k-card" width={w} height={h} rx="16" />
      <circle className="k-soft" cx={h / 2} cy={h / 2} r={h * 0.27} />
      <circle className="k-person" cx={h / 2} cy={h / 2 - 5} r={h * 0.09} />
      <path className="k-person" d={`M${h / 2 - 12} ${h / 2 + 14} q12 -17 24 0z`} />
      <rect className="k-text" x={h + 4} y={h / 2 - 11} width={w - h - 30} height="7" rx="3.5" />
      <rect className="k-text faint" x={h + 4} y={h / 2 + 4} width={(w - h - 30) * 0.66} height="7" rx="3.5" />
    </Sprite>
  );
}

/** Карточка-иконка (квадратная или широкая со строками «текста»). */
function IconCard({ x, y, kind, w = 74, h = 64, lines = false, cls = "" }: { x: number; y: number; kind: IconKind; w?: number; h?: number; lines?: boolean; cls?: string }) {
  return (
    <Sprite x={x} y={y} w={w} h={h} cls={`ls-float ${cls}`}>
      <rect className="k-card" width={w} height={h} rx="14" />
      <g transform={`translate(${lines ? 38 : w / 2} ${h / 2})`}><Icon kind={kind} /></g>
      {lines && <><rect className="k-text" x="68" y={h / 2 - 9} width={w - 90} height="6" rx="3" /><rect className="k-text faint" x="68" y={h / 2 + 4} width={(w - 90) * 0.7} height="6" rx="3" /></>}
    </Sprite>
  );
}

function Avatar({ x, y, r = 34, cls = "" }: { x: number; y: number; r?: number; cls?: string }) {
  return (
    <Sprite x={x - r} y={y - r} w={2 * r} h={2 * r} cls={`ls-float ${cls}`}>
      <g transform={`translate(${r} ${r})`}>
        <circle className="k-card" r={r - 1} />
        <circle className="k-soft" r={r - 8} />
        <circle className="k-person" cy={-r * 0.2} r={r * 0.24} />
        <path className="k-person" d={`M${-r * 0.48} ${r * 0.62} q${r * 0.48} ${-r * 0.74} ${r * 0.96} 0z`} />
      </g>
    </Sprite>
  );
}

/** Узел сети: мягкий ореол «пульсирует» (масштаб и прозрачность), ядро — в рисунке сцены. */
function Pulse({ x, y, d = 0 }: { x: number; y: number; d?: number }) {
  return <Sprite x={x - 14} y={y - 14} w={28} h={28} cls={`ls-pulse d${d % 4}`}><circle className="k-halo" cx="14" cy="14" r="12" /></Sprite>;
}

// ── 1. корпоративная сеть ───────────────────────────────────────────────────────────────────
const NET_PATHS = [
  "M40 520 C140 360 200 250 330 170 S560 70 760 60",
  "M760 60 C980 50 1180 70 1330 150 S1520 330 1560 520",
  "M1560 520 C1520 690 1420 820 1250 890 S940 960 760 950",
  "M760 950 C560 960 360 930 230 850 S60 680 40 520",
  "M330 170 C420 300 360 420 250 480",
  "M1330 150 C1240 300 1280 400 1380 470",
  "M230 850 C330 760 420 760 520 800",
  "M1250 890 C1150 790 1100 780 1040 820",
];
const NET_NODES: [number, number][] = [[330, 170], [760, 60], [1330, 150], [1560, 520], [1250, 890], [760, 950], [230, 850], [40, 520], [250, 480], [1380, 470], [520, 800], [1040, 820]];

const NETWORK: [() => ReactNode, () => ReactNode] = [
  () => (
    <>
      <Glow id="n-g1" cx={260} cy={240} r={420} />
      <Glow id="n-g2" cx={1380} cy={820} r={460} />
      {NET_PATHS.map((d, i) => <path key={i} d={d} className={`k-line${i > 3 ? " thin" : ""}`} />)}
      {NET_NODES.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="3.2" className="k-node-core" />)}
    </>
  ),
  () => (
    <>
      <Light x={300} y={260} r={260} cls="l1" />
      <Light x={1330} y={790} r={300} cls="l2" />
      {NET_NODES.map(([x, y], i) => <Pulse key={i} x={x} y={y} d={i} />)}
      <ProfileCard x={90} y={110} cls="f1" />
      <ProfileCard x={60} y={620} w={200} cls="f2" />
      <ProfileCard x={1290} y={600} cls="f3" />
      <IconCard x={1270} y={95} kind="group" w={200} h={74} lines cls="f4" />
      <IconCard x={560} y={835} kind="chart" w={210} h={74} lines cls="f2" />
      <IconCard x={1090} y={40} kind="chat" cls="f3" />
    </>
  ),
];

// ── 2. совместная работа ────────────────────────────────────────────────────────────────────
function Person({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      <path className="k-shirt" d="M-34 70 q4 -48 34 -50 q30 2 34 50z" />
      <circle className="k-skin" cy="-6" r="17" />
      <path className="k-hair" d="M-17 -10 q2 -20 19 -19 q17 1 15 18 q-6 -10 -18 -9 q-10 0 -16 10z" />
    </g>
  );
}

function Laptop({ x, y, s = 1, flip = false }: { x: number; y: number; s?: number; flip?: boolean }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${flip ? -s : s} ${s})`}>
      <path className="k-device" d="M-6 -46 l52 0 l10 44 l-52 0z" />
      <path className="k-device-edge" d="M-12 -2 h74 v5 h-74z" />
    </g>
  );
}

function Leaves({ x, y, s = 1, flip = false }: { x: number; y: number; s?: number; flip?: boolean }) {
  return (
    <Sprite x={x - 10} y={y - 140 * s} w={90 * s} h={140 * s} cls="ls-sway">
      <g transform={`translate(${flip ? 80 * s : 10} ${140 * s}) scale(${flip ? -s : s} ${s})`}>
        <path className="k-stem" d="M0 0 C6 -40 14 -80 34 -118" />
        {[[8, -30, -30], [14, -58, 28], [22, -84, -24], [30, -108, 30]].map(([lx, ly, a], i) => (
          <path key={i} className="k-leaf" transform={`translate(${lx} ${ly}) rotate(${a})`} d="M0 0 C10 -16 32 -18 44 -6 C30 4 12 6 0 0z" />
        ))}
      </g>
    </Sprite>
  );
}

const TEAM: [() => ReactNode, () => ReactNode] = [
  () => (
    <>
      <Glow id="t-g1" cx={330} cy={260} r={380} />
      <Glow id="t-g2" cx={1330} cy={760} r={420} />
      <path className="k-line dashed" d="M300 260 C420 460 380 680 470 790" />
      <path className="k-line dashed" d="M1300 290 C1200 460 1260 640 1220 800" />
      <path className="k-line dashed" d="M330 120 C560 40 900 40 1180 120" />
      <path className="k-table" d="M290 925 h330" />
    </>
  ),
  () => (
    <>
      <Light x={330} y={260} r={240} cls="l1" />
      <Light x={1330} y={760} r={280} cls="l2" />
      {/* человек с ноутбуком на видеосвязи */}
      <Sprite x={60} y={40} w={250} h={200} cls="ls-float f1">
        <rect className="k-card" width="250" height="190" rx="18" />
        <Person x={115} y={88} s={1.25} />
        <Laptop x={136} y={174} s={1.1} />
        <g transform="translate(222 34)"><rect className="k-badge" x="-22" y="-18" width="44" height="36" rx="10" /><g transform="scale(.62)"><Icon kind="camera" /></g></g>
      </Sprite>
      <Leaves x={40} y={440} s={1.1} />
      {/* видеовстреча на четверых */}
      <Sprite x={1210} y={60} w={300} h={200} cls="ls-float f3">
        <rect className="k-card strong" width="300" height="200" rx="14" />
        <circle className="k-dot" cx="16" cy="12" r="3" /><circle className="k-dot" cx="27" cy="12" r="3" /><circle className="k-dot" cx="38" cy="12" r="3" />
        {[[8, 22], [150, 22], [8, 108], [150, 108]].map(([cx, cy], i) => (
          <g key={i}>
            <rect className="k-screen" x={cx} y={cy} width="142" height="80" rx="8" />
            <clipPath id={`tv-${i}`}><rect x={cx} y={cy} width="142" height="80" rx="8" /></clipPath>
            <g clipPath={`url(#tv-${i})`}><Person x={cx + 71} y={cy + 40} s={0.72} /></g>
          </g>
        ))}
      </Sprite>
      <Leaves x={1490} y={360} s={0.9} flip />
      {/* чат: «печатает…» */}
      <Sprite x={1330} y={420} w={170} h={62} cls="ls-float f2">
        <rect className="k-card" width="170" height="62" rx="20" />
        {[0, 1, 2].map((i) => <circle key={i} className={`k-ink-fill typing t${i}`} cx={58 + i * 26} cy="31" r="7" />)}
      </Sprite>
      {/* человек с ноутбуком справа */}
      <Sprite x={1360} y={520} w={170} h={190} cls="ls-float f4">
        <Person x={80} y={55} s={1.2} />
        <Laptop x={35} y={160} s={1.2} flip />
      </Sprite>
      {/* задачи и диаграмма */}
      <IconCard x={70} y={600} kind="list" w={210} h={84} lines cls="f2" />
      <Sprite x={1250} y={820} w={210} h={120} cls="ls-float f1">
        <rect className="k-card" width="210" height="120" rx="16" />
        {[0.35, 0.5, 0.42, 0.72, 0.9].map((v, i) => <rect key={i} className="k-bar" x={30 + i * 34} y={100 - 70 * v} width="20" height={70 * v} rx="4" />)}
      </Sprite>
      {/* двое за столом */}
      <Sprite x={300} y={720} w={320} h={210} cls="ls-float f3">
        <Person x={80} y={70} s={1.3} />
        <Person x={200} y={80} s={1.25} />
        <Laptop x={155} y={170} s={1.15} />
      </Sprite>
      <Leaves x={170} y={990} s={0.9} />
    </>
  ),
];

// ── 3. волны, сетка, орбиты ─────────────────────────────────────────────────────────────────
/** Волна на два периода холста: сдвигается на один период по кругу — шов не виден. */
function wave(y: number, amp: number, len: number): string {
  let d = `M0 ${y}`;
  for (let x = 0; x < 3200; x += len) d += ` q${len / 4} ${-amp} ${len / 2} 0 t${len / 2} 0`;
  return d + " V1000 H0z";
}
const WAVE_SPEC: [number, number, number][] = [[780, 46, 800], [830, 34, 1600 / 3], [875, 26, 800]];      // период делит 1600 нацело — сдвиг на 1600 без шва

const WAVES: [() => ReactNode, () => ReactNode] = [
  () => (
    <>
      <Glow id="w-g1" cx={1200} cy={230} r={520} k={1.2} />
      <Glow id="w-g2" cx={260} cy={820} r={420} />
      <defs>
        <radialGradient id="w-orb" cx="0.38" cy="0.32" r="0.75">
          <stop offset="0" className="g-surface" style={{ stopOpacity: 0.85 }} />
          <stop offset="0.55" className="g-stop" style={{ stopOpacity: 0.12 }} />
          <stop offset="1" className="g-stop" style={{ stopOpacity: 0.02 }} />
        </radialGradient>
      </defs>
      <circle cx="1190" cy="250" r="230" fill="url(#w-orb)" className="k-orb" />
      <ellipse className="k-orbit" cx="1190" cy="250" rx="420" ry="120" transform="rotate(-18 1190 250)" />
      <ellipse className="k-orbit" cx="1190" cy="250" rx="330" ry="80" transform="rotate(12 1190 250)" />
      <g className="k-mesh">
        {Array.from({ length: 13 }, (_, i) => <path key={`v${i}`} d={`M${980 + i * 52} 1000 L${1180 + i * 30} 700`} />)}
        {Array.from({ length: 7 }, (_, i) => { const t = i / 6; return <path key={`h${i}`} d={`M${980 + 200 * t * t} ${1000 - 300 * t * t} H1600`} />; })}
      </g>
    </>
  ),
  () => (
    <>
      <Light x={1190} y={250} r={330} cls="l1" />
      <Light x={260} y={820} r={300} cls="l2" />
      {/* волны: полоса вдвое шире холста, сдвигается на период */}
      {WAVE_SPEC.map(([y, amp, len], i) => (
        <Sprite key={i} x={0} y={y - amp - 4} w={3200} h={1004 - y + amp} cls={`ls-wave w${i}`}>
          <path className={`k-wave w${i}`} transform={`translate(0 ${-(y - amp - 4)})`} d={wave(y, amp, len)} />
        </Sprite>
      ))}
      {/* точки на орбитах и искры — мерцают */}
      {[[1600, 165], [775, 335], [1515, 220], [870, 280], [180, 160], [420, 90], [90, 420], [760, 70]].map(([x, y], i) => (
        <Sprite key={i} x={x - 8} y={y - 8} w={16} h={16} cls={`ls-twinkle d${i % 4}`}><circle className="k-spark" cx="8" cy="8" r={i < 4 ? 5 : 3} /></Sprite>
      ))}
    </>
  ),
];

/** Телефон и планшет: та же «волна» — пятно света, шар и две волны, без движения. */
function MobileWaves() {
  return (
    <svg className="ls-mobile" viewBox="0 0 400 800" preserveAspectRatio="xMidYMid slice" focusable="false">
      <Glow id="m-g1" cx={330} cy={120} r={300} k={1.1} />
      <Glow id="m-g2" cx={60} cy={720} r={260} />
      <ellipse className="k-orbit" cx="320" cy="140" rx="200" ry="56" transform="rotate(-18 320 140)" />
      <path className="k-wave w0" d="M0 690 q100 -40 200 0 t200 0 V800 H0z" />
      <path className="k-wave w1" d="M0 730 q100 -28 200 0 t200 0 V800 H0z" />
    </svg>
  );
}

// ── 4. схема экосистемы ─────────────────────────────────────────────────────────────────────
const SCHEMA_PATHS = [
  "M200 300 V180 H520", "M594 162 H700 V120 H1060", "M1144 120 H1300 V180 H1400 V300",
  "M1400 640 V700", "M1400 794 V830 H1200", "M1050 835 H750", "M640 870 H300 V820 H200 V640",
  "M200 300 V640", "M1400 300 V430", "M1400 494 V640",
];
const SCHEMA_NODES: [number, number][] = [[200, 180], [700, 120], [1300, 180], [1400, 830], [300, 870], [200, 820]];

const SCHEMA: [() => ReactNode, () => ReactNode] = [
  () => (
    <>
      <defs>
        <pattern id="s-grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" className="k-grid" /></pattern>
      </defs>
      <rect width="1600" height="1000" fill="url(#s-grid)" className="k-grid-bg" />
      <Glow id="s-g1" cx={800} cy={500} r={620} k={0.7} />
      <circle cx="800" cy="500" r="400" className="k-orbit faint" />
      <circle cx="800" cy="500" r="490" className="k-orbit faint" />
      {SCHEMA_PATHS.map((d, i) => <path key={i} d={d} className="k-line dashed" />)}
      {SCHEMA_NODES.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="3.2" className="k-node-core" />)}
    </>
  ),
  () => (
    <>
      <Light x={800} y={500} r={420} cls="l1" />
      {SCHEMA_NODES.map(([x, y], i) => <Pulse key={i} x={x} y={y} d={i} />)}
      <IconCard x={520} y={130} kind="calendar" cls="f1" />
      <IconCard x={1060} y={88} kind="camera" w={84} cls="f3" />
      <IconCard x={1363} y={430} kind="chart" cls="f2" />
      <IconCard x={640} y={835} kind="group" w={110} cls="f4" />
      <IconCard x={1050} y={800} kind="list" w={150} h={70} lines cls="f1" />
      <Avatar x={200} y={300} cls="f2" />
      <Avatar x={200} y={640} cls="f4" />
      <Avatar x={1400} y={300} cls="f1" />
      <Avatar x={1400} y={740} cls="f3" />
    </>
  ),
];
