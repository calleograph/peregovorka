// Встроенная мини-игра «Кристаллики» (служебный модуль): окно, графика, эффекты, звук, ввод. Логика — в engine.js.
// Всё живёт в Shadow DOM плавающего окна; при закрытии освобождаются обработчики, анимации, аудио и холсты.
import { CrystalsEngine } from './engine.js';

/* «Кристаллики»: окно, графика, эффекты, звук, ввод. Логика — в CrystalsEngine выше. */
const game = (function () {
  'use strict';
  const ENG = CrystalsEngine, CFG = ENG.CONFIG, NN = CFG.size, T = CFG.timing;
  const FF = '"Segoe UI", "Nunito", "Trebuchet MS", "DejaVu Sans", system-ui, sans-serif';
  // Логические размеры (в «u»): всё окно масштабируется одним множителем u.
  const W0 = 440, H0 = 526, BAR = 34, BX = 16, BY = 70, BS = 408, CS = BS / NN, SPR = 1.5;
  const MIN_U = 0.8, MAX_U = 1.8;

  const store = {
    get(k, d) { try { const v = localStorage.getItem('crystals.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('crystals.' + k, JSON.stringify(v)); } catch (e) { /* приватный режим — живём без сохранения */ } },
  };

  const PAL = [
    { name: 'красных', base: '#ee1d43', light: '#ff9aa8', dark: '#6e0018', glow: '#ff3d5e' },
    { name: 'синих', base: '#2178ff', light: '#a6d4ff', dark: '#0a2580', glow: '#4a90ff' },
    { name: 'зелёных', base: '#1fcf55', light: '#aaffc0', dark: '#035a25', glow: '#3cf079' },
    { name: 'золотых', base: '#ffc61f', light: '#fff7b8', dark: '#9a4f00', glow: '#ffd84a' },
    { name: 'фиолетовых', base: '#a540ff', light: '#e8c2ff', dark: '#41088a', glow: '#c06bff' },
    { name: 'голубых', base: '#19dcf2', light: '#c8fdff', dark: '#035e73', glow: '#56f3ff' },
  ];
  // Индексы цветов частиц: 0–5 кристаллы, 6 белый, 7 золото, 8 лёд, 9 камень, 10 металл
  const EXTRA = ['#ffffff', '#ffd24a', '#bfeaff', '#9b86c9', '#d7dbe8'];
  const colorOf = (k) => (k < 6 ? PAL[k].glow : EXTRA[k - 6]);
  const lightOf = (k) => (k < 6 ? PAL[k].light : EXTRA[k - 6]);

  const hexRgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const mix = (a, b, t) => { const A = hexRgb(a), B = hexRgb(b); return 'rgb(' + A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(',') + ')'; };
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const easeOut = (p) => 1 - (1 - p) * (1 - p) * (1 - p);
  const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
  const fmt = (n) => Math.round(n).toLocaleString('ru-RU');
  const rnd = (a, b) => a + Math.random() * (b - a);
  const rc = (i) => [(i / NN) | 0, i % NN];
  const cellX = (c) => BX + (c + 0.5) * CS, cellY = (r) => BY + (r + 0.5) * CS;

  // ---------- Формы огранки: у каждого цвета своя ----------
  const circ = (n, r, off) => Array.from({ length: n }, (_, k) => [Math.cos(off + k * 2 * Math.PI / n) * r, Math.sin(off + k * 2 * Math.PI / n) * r]);
  function cutTri() {
    const P = circ(3, 1.04, -Math.PI / 2).map(([x, y]) => [x, y + 0.16]), out = [];
    const lerp = (p, q, t) => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
    for (let k = 0; k < 3; k++) { const a = P[k], b = P[(k + 1) % 3], c = P[(k + 2) % 3]; out.push(lerp(a, c, 0.14), lerp(a, b, 0.14)); }
    return out;
  }
  const SHAPES = [
    [[-0.9, -0.36], [-0.52, -0.84], [0.52, -0.84], [0.9, -0.36], [0, 0.92]], // красный — щит
    circ(12, 0.9, Math.PI / 12), // синий — круглый бриллиант
    [[-0.46, -0.86], [0.46, -0.86], [0.8, -0.52], [0.8, 0.52], [0.46, 0.86], [-0.46, 0.86], [-0.8, 0.52], [-0.8, -0.52]], // зелёный — изумруд
    circ(6, 0.95, -Math.PI / 2), // золотой — шестигранник
    cutTri(), // фиолетовый — триллион
    [[0, -0.98], [0.76, 0], [0, 0.98], [-0.76, 0]], // голубой — ромб
  ];
  const TABLE_K = [0.5, 0.56, 0.56, 0.52, 0.42, 0.4];

  // ---------- Иконки интерфейса ----------
  const ICON = {
    gem: '<svg viewBox="0 0 24 24"><defs><linearGradient id="ig" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f3c6ff"/><stop offset=".5" stop-color="#b44dff"/><stop offset="1" stop-color="#5b12b0"/></linearGradient></defs><path d="M12 2l8 5v10l-8 5-8-5V7z" fill="url(#ig)"/><path d="M12 2v6l8-1M12 8L4 7M12 8v14" stroke="#fff" stroke-opacity=".5" stroke-width="1" fill="none"/></svg>',
    sndOff: '<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 4V5L7 9H3z" fill="currentColor"/><path d="M16 9.5l5 5M21 9.5l-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    sndOn: '<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 4V5L7 9H3z" fill="currentColor"/><path d="M15.5 8.8a4.5 4.5 0 0 1 0 6.4M18.3 6a8.4 8.4 0 0 1 0 12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
    min: '<svg viewBox="0 0 24 24"><path d="M6 12h12" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg>',
    close: '<svg viewBox="0 0 24 24"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/></svg>',
    play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.4-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z" fill="currentColor"/></svg>',
    trophy: '<svg viewBox="0 0 24 24"><path d="M7 3h10v5a5 5 0 0 1-10 0z" fill="currentColor"/><path d="M7 5H4v1.5A3.5 3.5 0 0 0 7.5 10M17 5h3v1.5a3.5 3.5 0 0 1-3.5 3.5" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M10.5 12.5h3l.5 4h-4z M7.5 18h9a1 1 0 0 1 1 1v2h-11v-2a1 1 0 0 1 1-1z" fill="currentColor"/></svg>',
    gear: '<svg viewBox="0 0 24 24"><g fill="currentColor">' + [0, 45, 90, 135, 180, 225, 270, 315].map((a) => `<rect x="10.4" y="1.6" width="3.2" height="5" rx="1" transform="rotate(${a} 12 12)"/>`).join('') + '<circle cx="12" cy="12" r="6.6"/></g><circle cx="12" cy="12" r="2.6" fill="#2b1a73"/></svg>',
    help: '<svg viewBox="0 0 24 24"><path d="M8.6 8.8a3.5 3.5 0 1 1 5 3.2c-1 .5-1.6 1.2-1.6 2.3v.6" stroke="currentColor" stroke-width="2.8" fill="none" stroke-linecap="round"/><circle cx="12" cy="19.2" r="1.8" fill="currentColor"/></svg>',
    replay: '<svg viewBox="0 0 24 24"><path d="M5.6 9.2A7 7 0 1 1 5 13.5" stroke="currentColor" stroke-width="2.8" fill="none" stroke-linecap="round"/><path d="M3.5 4.5v6h6z" fill="currentColor"/></svg>',
    toHome: '<svg viewBox="0 0 24 24"><path d="M3 11.2L12 3.8l9 7.4V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" fill="currentColor"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    star: (cls) => `<svg class="${cls}" viewBox="0 0 100 100"><path d="M50 5l12.6 30 32.4 2.7-24.6 21.2 7.5 31.6L50 73.6 22.1 90.5l7.5-31.6L5 37.7l32.4-2.7z" class="sf" stroke-width="5" stroke-linejoin="round"/><path d="M50 16l8 19 18 2-11 7" class="sh" fill="none" stroke-width="5" stroke-linecap="round"/></svg>`,
  };

  // ---------- Стили окна (единицы «u» превращаются в calc(var(--u) * N)) ----------
  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .defs { position: absolute; }
  .win { --ff: ${FF}; position: fixed; z-index: 1250; left: 0; top: 0; width: 440u; height: ${BAR + H0}u; transform: translate3d(var(--x, 0px), var(--y, 0px), 0);
    border-radius: 18u; overflow: hidden; background: #0d0826; color: #f3eaff; font-family: var(--ff); user-select: none; -webkit-user-select: none;
    box-shadow: 0 0 0 1px rgba(205,175,255,.62), 0 0 18u rgba(150,90,255,.45), 0 24px 60px rgba(0,0,0,.55); outline: none; -webkit-tap-highlight-color: transparent; }
  .win.min { height: ${BAR}u; }
  .win.min .body, .win.min .grip { display: none; }
  .bar { position: relative; height: ${BAR}u; display: flex; align-items: center; gap: 6u; padding: 0 5u 0 11u; cursor: grab; touch-action: none;
    background: linear-gradient(#2e1b68, #1b1046); border-bottom: 1px solid rgba(190,150,255,.28); }
  .bar.drag { cursor: grabbing; }
  .ico { width: 20u; height: 20u; flex: none; filter: drop-shadow(0 0 4u rgba(200,120,255,.7)); }
  .ico svg { width: 100%; height: 100%; display: block; }
  .ttl { flex: 1; font-weight: 700; font-size: 14.5u; color: #f6efff; letter-spacing: .01em; white-space: nowrap; }
  button { font-family: inherit; }
  .tb { width: 30u; height: 26u; border-radius: 8u; border: 0; background: transparent; color: #cdbdf5; display: grid; place-items: center; cursor: pointer; padding: 0;
    transition: background-color .15s, color .15s, transform .1s; }
  .tb svg { width: 17u; height: 17u; }
  .tb:hover { background: rgba(255,255,255,.12); color: #fff; }
  .tb.cls { color: #ff8a9c; }
  .tb.cls:hover { background: #e3345a; color: #fff; }
  .tb:active { transform: scale(.9); }
  .tb.snd.on { color: #ffd84a; }
  .tb:focus-visible, .rb:focus-visible, .seg button:focus-visible, .sw:focus-visible, .back:focus-visible, .hb:focus-visible { outline: 2u solid #ffe27a; outline-offset: 2u; }
  .body { position: absolute; left: 0; top: ${BAR}u; width: 440u; height: ${H0}u; overflow: hidden; background: #0d0826; }
  canvas.main { position: absolute; left: 0; top: 0; width: 100%; height: 100%; display: block; touch-action: none; }
  canvas.main.pixel { image-rendering: pixelated; image-rendering: crisp-edges; }
  .grip { position: absolute; right: 0; bottom: 0; width: 22u; height: 22u; cursor: nwse-resize; touch-action: none; z-index: 5;
    background: linear-gradient(135deg, transparent 50%, rgba(200,170,255,.35) 50% 58%, transparent 58% 68%, rgba(200,170,255,.35) 68% 76%, transparent 76%); }

  .scr { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; z-index: 1;
    transition: opacity .32s ease, transform .32s ease, visibility 0s; }
  .scr.off { opacity: 0; transform: scale(1.03); visibility: hidden; pointer-events: none; transition: opacity .28s ease, transform .28s ease, visibility 0s .28s; }
  .game { pointer-events: none; }
  .game > * { pointer-events: auto; }
  .game > .hud { pointer-events: none; }

  /* Стартовый экран */
  .title { display: grid; margin: 172u 0 0; font-size: 47u; font-weight: 900; line-height: 1.1; letter-spacing: -.005em; transform: rotate(-2.5deg); }
  .title > span { grid-area: 1 / 1; position: relative; text-align: center; white-space: nowrap; }
  .t-back { color: #3c0f7c; -webkit-text-stroke: 8u #3c0f7c; filter: drop-shadow(0 0 10u rgba(200,100,255,.75)) drop-shadow(0 5u 0 #1a053c); }
  .t-front { color: transparent; background: linear-gradient(#fffce6 8%, #ffe066 40%, #ffa41c 72%, #ffd25a 100%); -webkit-background-clip: text; background-clip: text; }
  .sub { margin-top: 10u; font-size: 15u; font-weight: 600; color: #ddd0ff; text-shadow: 0 1u 6u rgba(0,0,0,.6); text-align: center; }
  .btn { position: relative; margin-top: 20u; height: 60u; min-width: 216u; padding: 0 34u; border: 0; border-radius: 30u; cursor: pointer; overflow: hidden;
    display: inline-flex; align-items: center; justify-content: center; gap: 10u; font: 800 27u/1 var(--ff); color: #fff;
    background: linear-gradient(#93ff70, #34c63d 47%, #189c27 53%, #11821f);
    box-shadow: inset 0 2u 0 rgba(255,255,255,.7), inset 0 -4u 0 rgba(0,70,10,.45), 0 5u 0 #0a5514, 0 10u 22u rgba(0,0,0,.45), 0 0 24u rgba(90,255,100,.3);
    text-shadow: 0 2u 0 #0b6a17, 0 0 8u rgba(0,60,0,.35); transition: transform .12s, filter .15s, box-shadow .12s; }
  .btn svg { width: 24u; height: 24u; filter: drop-shadow(0 2u 0 #0b6a17); }
  .btn::after { content: ''; position: absolute; top: 0; left: -70%; width: 45%; height: 100%; background: linear-gradient(100deg, transparent, rgba(255,255,255,.55), transparent); transform: skewX(-20deg); transition: left .6s ease; pointer-events: none; }
  .btn:hover { filter: brightness(1.07) saturate(1.1); transform: translateY(-1u); }
  .btn:hover::after { left: 135%; }
  .btn:active { transform: translateY(4u); box-shadow: inset 0 2u 0 rgba(255,255,255,.7), inset 0 -3u 0 rgba(0,70,10,.45), 0 1u 0 #0a5514, 0 4u 10u rgba(0,0,0,.4); }
  .btn:focus-visible { outline: 3u solid #ffe27a; outline-offset: 4u; }
  .btn.breath { animation: breath 2.6s ease-in-out infinite; }
  @keyframes breath { 50% { box-shadow: inset 0 2u 0 rgba(255,255,255,.7), inset 0 -4u 0 rgba(0,70,10,.45), 0 5u 0 #0a5514, 0 10u 22u rgba(0,0,0,.45), 0 0 38u rgba(110,255,120,.55); } }
  .best { margin-top: 18u; display: flex; align-items: center; gap: 8u; font-size: 17u; font-weight: 800; color: #ffe27a; text-shadow: 0 2u 6u rgba(0,0,0,.6); font-variant-numeric: tabular-nums; }
  .best svg { width: 24u; height: 24u; color: #ffc61f; filter: drop-shadow(0 0 6u rgba(255,200,40,.6)); }
  .row3 { margin-top: auto; margin-bottom: 20u; display: flex; gap: 22u; }
  .rb { width: 52u; height: 52u; border-radius: 50%; border: 2u solid #a891ff; cursor: pointer; display: grid; place-items: center; padding: 0; color: #efe8ff;
    background: radial-gradient(circle at 50% 30%, #7457ee, #3c26a6 62%, #24156c);
    box-shadow: inset 0 2u 0 rgba(255,255,255,.35), 0 4u 0 #160b45, 0 6u 14u rgba(0,0,0,.45); transition: transform .12s, filter .15s; }
  .rb svg { width: 26u; height: 26u; }
  .rb.gold { color: #ffcf33; }
  .rb:hover { filter: brightness(1.15); transform: translateY(-2u) rotate(-4deg); }
  .rb:active { transform: translateY(3u); box-shadow: inset 0 2u 0 rgba(255,255,255,.35), 0 1u 0 #160b45; }

  /* Игровой экран */
  .hud { position: absolute; left: 12u; right: 12u; top: 9u; height: 52u; display: flex; gap: 8u; }
  .badge { flex: none; width: 76u; border-radius: 14u; display: flex; flex-direction: column; align-items: center; justify-content: center; line-height: 1;
    background: linear-gradient(#3d2789, #22135c); border: 1.5u solid rgba(175,145,255,.62); box-shadow: inset 0 1u 0 rgba(255,255,255,.28), 0 3u 8u rgba(0,0,0,.35); }
  .badge small { font-size: 11.5u; font-weight: 700; color: #cbbaff; }
  .badge b { margin-top: 3u; font-size: 23u; font-weight: 900; color: #fff; font-variant-numeric: tabular-nums; }
  .moves.low b { color: #ff8193; animation: lowpulse 1s ease-in-out infinite; }
  @keyframes lowpulse { 50% { transform: scale(1.12); } }
  .mid { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 7u; }
  .score { text-align: center; font-size: 14.5u; font-weight: 700; color: #e6dcff; white-space: nowrap; }
  .score b { font-size: 19u; font-weight: 900; color: #ffe066; font-variant-numeric: tabular-nums; text-shadow: 0 0 8u rgba(255,200,60,.4); }
  .prog { position: relative; height: 14u; margin: 0 10u; border-radius: 7u; background: rgba(6,2,22,.75); border: 1.5u solid rgba(175,145,255,.5); }
  .prog > i { position: absolute; left: 1u; top: 1u; bottom: 1u; width: 0; max-width: calc(100% - 2u); border-radius: 6u; background: linear-gradient(#fff07a, #ffb81c 60%, #f08c00); box-shadow: 0 0 8u rgba(255,190,40,.6); transition: width .35s ease; }
  .prog .ps { position: absolute; top: 50%; width: 22u; height: 22u; transform: translate(-50%, -50%); }
  .prog .ps .sf { fill: #4a3d78; stroke: #2b2050; } .prog .ps .sh { stroke: transparent; }
  .prog .ps.on .sf { fill: #ffd23a; stroke: #b86b00; } .prog .ps.on .sh { stroke: rgba(255,255,255,.75); }
  .prog .ps.on { animation: starpop .45s cubic-bezier(.3,1.8,.5,1); }
  @keyframes starpop { 0% { transform: translate(-50%, -50%) scale(.4); } 100% { transform: translate(-50%, -50%) scale(1); } }
  .status { position: absolute; left: 14u; right: 12u; top: 487u; height: 34u; display: flex; align-items: center; gap: 7u; }
  .status .lbl { font-size: 13u; font-weight: 700; color: #bba9ef; }
  .goals { flex: 1; min-width: 0; display: flex; gap: 6u; overflow: hidden; }
  .goal { flex: none; display: flex; align-items: center; gap: 4u; height: 30u; padding: 0 10u 0 4u; border-radius: 15u; font-size: 15u; font-weight: 800; font-variant-numeric: tabular-nums;
    background: rgba(32,18,86,.85); border: 1px solid rgba(175,145,255,.38); color: #fff; }
  .goal img { width: 24u; height: 24u; display: block; }
  .goal.done { border-color: #6cf08a; color: #a8ffbc; }
  .goal.done b::after { content: ' ✓'; }
  .hb { flex: none; width: 32u; height: 32u; border-radius: 50%; border: 1.5u solid #9d86ff; background: radial-gradient(circle at 50% 30%, #6a4fe0, #321f8f); color: #fff; cursor: pointer; display: grid; place-items: center; padding: 0; }
  .hb svg { width: 18u; height: 18u; }
  .hb:hover { filter: brightness(1.2); }
  .card { position: absolute; left: 50%; top: ${BY + BS / 2}u; width: 316u; transform: translate(-50%, -50%); padding: 18u 20u 16u; border-radius: 20u; text-align: center; cursor: pointer;
    background: linear-gradient(rgba(52,30,128,.96), rgba(26,14,72,.96)); border: 1.5u solid rgba(190,160,255,.6); box-shadow: 0 14u 40u rgba(0,0,0,.55), 0 0 30u rgba(150,90,255,.35);
    transition: opacity .3s, transform .3s; }
  .card.off { opacity: 0; transform: translate(-50%, -50%) scale(.92); pointer-events: none; }
  .card h2 { margin: 0; font-size: 26u; font-weight: 900; color: #ffe066; }
  .card .cg { margin: 12u 0 4u; display: flex; justify-content: center; flex-wrap: wrap; gap: 6u; }
  .card .ct { margin: 10u 0 0; font-size: 14u; line-height: 1.35; color: #ddd0ff; text-wrap: balance; }
  .card .cgo { margin-top: 12u; font-size: 12u; color: #a897dc; }

  /* Поздравления */
  .fx { position: absolute; inset: 0; pointer-events: none; z-index: 3; overflow: hidden; }
  .praise { position: absolute; transform: translate(-50%, -50%); white-space: nowrap; font-weight: 900; letter-spacing: -.01em; color: transparent; animation: praise 1.25s ease-out forwards; }
  .praise::before, .praise::after { content: attr(data-t); position: absolute; left: 0; top: 0; }
  .praise::before { color: #3a0d78; -webkit-text-stroke: 7u #3a0d78; filter: drop-shadow(0 0 9u rgba(210,110,255,.85)) drop-shadow(0 4u 0 #1a053c); }
  .praise::after { background: linear-gradient(#fffde8 15%, #ffe15c 48%, #ff9e1a 85%); -webkit-background-clip: text; background-clip: text; color: transparent; }
  .praise.t1 { font-size: 27u; }
  .praise.t2 { font-size: 35u; }
  .praise.t3 { font-size: 43u; animation-duration: 1.7s; }
  .praise.t3::before { filter: drop-shadow(0 0 16u rgba(255,140,255,1)) drop-shadow(0 0 6u #fff) drop-shadow(0 5u 0 #1a053c); }
  .praise.info { font-size: 24u; animation-duration: 1.4s; }
  @keyframes praise { 0% { opacity: 0; transform: translate(-50%, -50%) scale(.3) rotate(-6deg); } 16% { opacity: 1; transform: translate(-50%, -50%) scale(1.14) rotate(2deg); }
    28% { transform: translate(-50%, -50%) scale(1) rotate(-1deg); } 72% { opacity: 1; transform: translate(-50%, -50%) scale(1) rotate(-1deg); } 100% { opacity: 0; transform: translate(-50%, -64%) scale(.96) rotate(-1deg); } }
  .reduce .praise { animation-name: praiseSoft; }
  .reduce .scr, .reduce .card { transition-duration: .01s; }
  .reduce .btn.breath, .reduce .moves.low b { animation: none; }
  @keyframes praiseSoft { 0% { opacity: 0; } 15%, 75% { opacity: 1; } 100% { opacity: 0; } }

  /* Победа и проигрыш */
  .stars { position: relative; width: 300u; height: 120u; margin-top: 18u; }
  .stars .big { position: absolute; width: 74u; height: 74u; top: 30u; opacity: 0; transform: scale(.2); }
  .stars .big.s0 { left: 20u; top: 38u; transform-origin: 50% 60%; rotate: -14deg; }
  .stars .big.s1 { left: 105u; top: 0; width: 92u; height: 92u; }
  .stars .big.s2 { left: 206u; top: 38u; rotate: 14deg; }
  .stars .big .sf { fill: #3d3170; stroke: #241a4d; } .stars .big .sh { stroke: transparent; }
  .stars .big.got .sf { fill: url(#gold); stroke: #a65a00; } .stars .big.got .sh { stroke: rgba(255,255,255,.8); }
  .stars .big.in { animation: bigstar .6s cubic-bezier(.25,1.7,.45,1) forwards; }
  @keyframes bigstar { 0% { opacity: 0; transform: scale(.2) translateY(30u); } 60% { opacity: 1; transform: scale(1.18) translateY(-6u); } 100% { opacity: 1; transform: scale(1) translateY(0); } }
  .ribbon { position: relative; margin-top: 8u; padding: 10u 34u 12u; font-size: 26u; font-weight: 900; color: #ffe066; text-shadow: 0 2u 0 #4a1590, 0 0 10u rgba(255,200,80,.4);
    background: linear-gradient(#8e4cf0, #5a23b8 60%, #45179a); border-radius: 10u; box-shadow: inset 0 2u 0 rgba(255,255,255,.35), 0 6u 14u rgba(0,0,0,.4); white-space: nowrap; }
  .ribbon::before, .ribbon::after { content: ''; position: absolute; top: 12u; width: 30u; height: 100%; background: #3b1386; z-index: -1; }
  .ribbon::before { left: -22u; clip-path: polygon(0 0, 100% 0, 100% 100%, 0 100%, 30% 50%); }
  .ribbon::after { right: -22u; clip-path: polygon(0 0, 100% 0, 70% 50%, 100% 100%, 0 100%); }
  .ribbon.sad { color: #d9cdff; background: linear-gradient(#4b3c94, #2f2470); text-shadow: 0 2u 0 #1b1146; }
  .ribbon.sad::before, .ribbon.sad::after { background: #241a5c; }
  .wscore { margin-top: 20u; font-size: 22u; font-weight: 800; color: #ffe8a0; font-variant-numeric: tabular-nums; }
  .wscore b { font-size: 30u; color: #ffe066; text-shadow: 0 0 12u rgba(255,200,60,.45); }
  .wtot { margin-top: 4u; font-size: 13.5u; color: #b9a8ec; font-variant-numeric: tabular-nums; }
  .phrase { margin-top: 12u; max-width: 330u; font-size: 19u; font-weight: 800; line-height: 1.25; text-align: center; color: #fff4d6; text-wrap: balance; text-shadow: 0 2u 8u rgba(0,0,0,.6); }
  .left { margin-top: 12u; display: flex; gap: 6u; flex-wrap: wrap; justify-content: center; }
  .row2 { margin-top: 16u; display: flex; gap: 26u; }
  .end .btn { margin-top: 20u; }
  .lose .ribbon { margin-top: 96u; }

  /* Лавка */
  .shop .head { justify-content: space-between; }
  .coins { display: flex; align-items: center; gap: 6u; height: 34u; padding: 0 14u 0 6u; border-radius: 17u; background: rgba(0,0,0,.35); border: 1px solid rgba(255,214,90,.45); font-size: 18u; font-weight: 900; color: #ffe066; font-variant-numeric: tabular-nums; }
  img.coin { width: 24u; height: 24u; }
  .beacons { width: 100%; display: flex; align-items: center; gap: 7u; padding: 0 16u 6u; font-size: 13u; color: #cbbaff; }
  .beacons i { width: 14u; height: 20u; border-radius: 4u 4u 2u 2u; background: #2b2160; border: 1px solid #5b4aa8; }
  .beacons i.lit { background: linear-gradient(#fff3a8, #ffb51c); border-color: #ffe27a; box-shadow: 0 0 10u rgba(255,200,60,.8); }
  .beacons span { margin-left: 6u; }
  .items { width: 100%; display: grid; grid-template-columns: repeat(2, 1fr); gap: 8u; padding: 4u 14u; }
  .item { position: relative; display: grid; grid-template-columns: 54u 1fr; grid-template-rows: auto auto auto; column-gap: 8u; align-items: center; text-align: left; padding: 8u 10u 8u 6u; min-height: 74u;
    border-radius: 14u; border: 1.5u solid rgba(175,145,255,.45); background: linear-gradient(rgba(70,45,160,.55), rgba(30,18,86,.75)); color: #fff; cursor: pointer; transition: transform .12s, filter .15s, border-color .15s; }
  .item:last-child { grid-column: 1 / -1; }
  .item canvas { grid-row: 1 / 4; width: 54u; height: 54u; }
  .item b { font-size: 14.5u; font-weight: 800; color: #ffe27a; }
  .item span { font-size: 12u; color: #d3c7f4; }
  .item em { font-style: normal; display: flex; align-items: center; gap: 3u; font-size: 14u; font-weight: 900; color: #ffe066; }
  .item em img.coin { width: 18u; height: 18u; }
  .item .cnt { position: absolute; right: 8u; top: 6u; font-style: normal; font-size: 15u; font-weight: 900; color: #9dffb0; }
  .item:hover { filter: brightness(1.15); transform: translateY(-1u); border-color: #ffe27a; }
  .item:active { transform: translateY(2u); }
  .item.poor { filter: saturate(.4) brightness(.75); }
  .item.no { animation: nope .35s; }
  @keyframes nope { 25% { transform: translateX(-4u); } 75% { transform: translateX(4u); } }
  .item:focus-visible { outline: 2u solid #ffe27a; outline-offset: 2u; }
  .shint { margin: 6u 18u 0; font-size: 12.5u; color: #b4a4e6; text-align: center; text-wrap: balance; }
  .shop .btn { margin-top: 10u; }
  .wcoins { margin-top: 6u; display: flex; align-items: center; gap: 6u; font-size: 16u; font-weight: 800; color: #ffe066; }
  .wcoins img { width: 22u; height: 22u; }
  .card .chap { font-size: 12u; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; color: #b99bff; margin-bottom: 2u; }
  .card .story { margin: 8u 0 0; font-size: 13.5u; line-height: 1.35; font-style: italic; color: #f0e6ff; text-wrap: balance; }

  /* Справка, настройки, рекорды */
  .panel { background: linear-gradient(rgba(13,7,38,.82), rgba(13,7,38,.94)); }
  .head { width: 100%; flex: none; display: flex; align-items: center; gap: 10u; padding: 12u 14u 8u; }
  .head h2 { margin: 0; font-size: 22u; font-weight: 900; color: #ffe066; }
  .back { width: 38u; height: 38u; border-radius: 12u; border: 1.5u solid #9d86ff; background: radial-gradient(circle at 50% 30%, #6a4fe0, #321f8f); color: #fff; cursor: pointer; display: grid; place-items: center; padding: 0; }
  .back svg { width: 20u; height: 20u; }
  .back:hover { filter: brightness(1.2); }
  .scroll { flex: 1; width: 100%; overflow-y: auto; padding: 2u 14u 12u; overscroll-behavior: contain; touch-action: pan-y; scrollbar-width: thin; scrollbar-color: #6a52c8 transparent; }
  .scroll::-webkit-scrollbar { width: 8u; } .scroll::-webkit-scrollbar-thumb { background: #5a44b0; border-radius: 4u; }
  .sec { margin: 6u 0 14u; }
  .sec h3 { margin: 0 0 8u; font-size: 16u; font-weight: 800; color: #e6dbff; }
  .sec p { margin: 0 0 8u; font-size: 13.5u; line-height: 1.42; color: #cfc2f2; text-wrap: pretty; }
  .hrow { display: grid; grid-template-columns: 136u 1fr; gap: 10u; align-items: center; padding: 7u 8u; margin-bottom: 6u; border-radius: 12u; background: rgba(255,255,255,.045); border: 1px solid rgba(170,140,255,.14); }
  .hrow canvas { width: 136u; height: 56u; display: block; }
  .hrow b { display: block; font-size: 14u; color: #ffe27a; margin-bottom: 2u; }
  .hrow span { display: block; font-size: 12.5u; line-height: 1.35; color: #d3c7f4; text-wrap: pretty; }
  .keys { display: grid; grid-template-columns: auto 1fr; gap: 5u 10u; font-size: 13u; color: #cfc2f2; }
  .keys kbd { align-self: start; font: 700 11.5u/1.5 var(--ff); padding: 0 6u; border-radius: 5u; background: #2d1d72; border: 1px solid #6c56c9; color: #fff; white-space: nowrap; justify-self: start; }
  .author { margin: 18u 0 0; text-align: right; font-size: 10u; color: rgba(220,205,255,.36); letter-spacing: .03em; }
  .opt { width: 100%; padding: 4u 18u; }
  .opt h3 { margin: 14u 0 8u; font-size: 15u; font-weight: 800; color: #e6dbff; }
  .opt p { margin: 8u 0 0; font-size: 12.5u; color: #b4a4e6; line-height: 1.4; text-wrap: pretty; }
  .seg { display: flex; gap: 4u; padding: 4u; border-radius: 14u; background: rgba(0,0,0,.35); border: 1px solid rgba(170,140,255,.3); }
  .seg button { flex: 1; height: 38u; border: 0; border-radius: 10u; background: transparent; color: #cbbcf5; font: 700 14u/1 var(--ff); cursor: pointer; }
  .seg button:hover { background: rgba(255,255,255,.07); }
  .seg button[aria-pressed="true"] { background: linear-gradient(#7a5cf5, #4a2fc0); color: #fff; box-shadow: inset 0 1u 0 rgba(255,255,255,.35); }
  .swrow { display: flex; align-items: center; justify-content: space-between; gap: 12u; margin-top: 18u; font-size: 15u; font-weight: 700; color: #e6dbff; }
  .sw { flex: none; width: 54u; height: 30u; border-radius: 15u; border: 0; padding: 0; cursor: pointer; background: #3a2c72; position: relative; transition: background-color .2s; }
  .sw::after { content: ''; position: absolute; left: 3u; top: 3u; width: 24u; height: 24u; border-radius: 50%; background: #fff; transition: transform .2s; box-shadow: 0 2u 4u rgba(0,0,0,.35); }
  .sw[aria-checked="true"] { background: #2fbf45; }
  .sw[aria-checked="true"]::after { transform: translateX(24u); }
  .seg button { font-size: 13u; }
  .vol { display: grid; grid-template-columns: 74u 1fr 38u; align-items: center; gap: 10u; margin-top: 8u; font-size: 14u; font-weight: 700; color: #e6dbff; }
  .vol output { font-size: 13u; color: #c9b8ff; text-align: right; font-variant-numeric: tabular-nums; }
  .vol input { width: 100%; accent-color: #8a6cff; height: 22u; cursor: pointer; }
  .recs { width: 100%; padding: 4u 18u; list-style: none; margin: 0; counter-reset: r; }
  .recs li { display: flex; align-items: center; gap: 12u; height: 50u; padding: 0 14u; margin-bottom: 8u; border-radius: 14u; background: rgba(255,255,255,.05); border: 1px solid rgba(170,140,255,.2); font-variant-numeric: tabular-nums; }
  .recs li::before { counter-increment: r; content: counter(r); width: 28u; height: 28u; border-radius: 50%; display: grid; place-items: center; font-weight: 900; font-size: 14u; background: #3b2a86; color: #e6dbff; }
  .recs li:first-child::before { background: linear-gradient(#ffe066, #e69500); color: #4a2600; }
  .recs .rs { flex: 1; font-size: 19u; font-weight: 900; color: #ffe066; }
  .recs .rl { font-size: 13u; color: #c3b4ef; text-align: right; line-height: 1.3; }
  .empty { margin-top: 40u; padding: 0 30u; text-align: center; font-size: 15u; color: #b4a4e6; line-height: 1.45; text-wrap: balance; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  `.replace(/(-?\d*\.?\d+)u\b/g, 'calc(var(--u) * $1)');

  const HTML = `
  <svg class="defs" width="0" height="0" aria-hidden="true"><defs><linearGradient id="gold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff5a8"/><stop offset=".45" stop-color="#ffd23a"/><stop offset="1" stop-color="#f08a00"/></linearGradient></defs></svg>
  <div class="win" tabindex="-1" role="dialog" aria-modal="false" aria-label="Кристаллики">
    <div class="bar">
      <span class="ico">${ICON.gem}</span><span class="ttl">Кристаллики</span>
      <button class="tb snd" type="button" aria-pressed="false" aria-label="Включить звук" title="Звук">${ICON.sndOff}</button>
      <button class="tb mn" type="button" aria-label="Свернуть" title="Свернуть">${ICON.min}</button>
      <button class="tb cls" type="button" aria-label="Закрыть игру" title="Закрыть">${ICON.close}</button>
    </div>
    <div class="body">
      <canvas class="main" aria-label="Игровое поле"></canvas>
      <section class="scr start">
        <h1 class="title"><span class="t-back" aria-hidden="true">Кристаллики</span><span class="t-front">Кристаллики</span></h1>
        <div class="sub">Собирай кристаллы. Создавай комбинации</div>
        <button class="btn breath play" type="button">${ICON.play}<span>Играть</span></button>
        <div class="best">${ICON.trophy}<span>Рекорд: <b class="bestv">0</b></span></div>
        <div class="row3">
          <button class="rb gold" type="button" data-go="records" aria-label="Рекорды" title="Рекорды">${ICON.trophy}</button>
          <button class="rb" type="button" data-go="settings" aria-label="Настройки" title="Настройки">${ICON.gear}</button>
          <button class="rb" type="button" data-go="help" aria-label="Справка" title="Справка">${ICON.help}</button>
        </div>
      </section>
      <section class="scr game off">
        <div class="hud">
          <div class="badge"><small>Уровень</small><b class="lv">1</b></div>
          <div class="mid">
            <div class="score">Очки: <b class="sc">0</b></div>
            <div class="prog"><i></i>${ICON.star('ps p0')}${ICON.star('ps p1')}${ICON.star('ps p2')}</div>
          </div>
          <div class="badge moves"><small>Ходы</small><b class="mv">0</b></div>
        </div>
        <div class="status"><span class="lbl">Цель:</span><div class="goals"></div>
          <button class="hb" type="button" data-go="help" aria-label="Справка" title="Справка">${ICON.help}</button></div>
        <div class="card off" role="status"></div>
      </section>
      <section class="scr end victory off">
        <div class="stars">${ICON.star('big s0')}${ICON.star('big s1')}${ICON.star('big s2')}</div>
        <div class="ribbon">Уровень пройден!</div>
        <div class="wscore">Очки: <b class="ws">0</b></div>
        <div class="wtot"></div>
        <div class="phrase"></div>
        <div class="wcoins"></div>
        <button class="btn next" type="button"><span>Дальше</span>${ICON.play}</button>
        <div class="row2">
          <button class="rb" type="button" data-act="retry" aria-label="Пройти уровень заново" title="Заново">${ICON.replay}</button>
          <button class="rb" type="button" data-act="home" aria-label="В меню" title="В меню">${ICON.toHome}</button>
        </div>
      </section>
      <section class="scr end lose off">
        <div class="ribbon sad">Ходы закончились</div>
        <div class="wscore">Очки: <b class="ls">0</b></div>
        <div class="phrase lp">Совсем чуть-чуть не хватило</div>
        <div class="left"></div>
        <button class="btn again" type="button">${ICON.replay}<span>Повторить</span></button>
        <div class="row2"><button class="rb" type="button" data-act="home" aria-label="В меню" title="В меню">${ICON.toHome}</button></div>
      </section>
      <section class="scr panel shop off" aria-label="Лавка усилителей">
        <div class="head"><h2>Лавка усилителей</h2><div class="coins"><img class="coin" alt="Осколки"><b class="cv">0</b></div></div>
        <div class="beacons"></div>
        <div class="items">${[['r', 'Ракета', 'Чистит линию'], ['p', 'Пропеллер', 'Летит к цели'], ['b', 'Бомба', 'Взрыв 5×5'], ['m', 'Мегакристалл', 'Убирает цвет'], ['moves', '+5 ходов', 'Больше времени']].map(([kk, n, t]) => `<button class="item" type="button" data-k="${kk}"><canvas data-h="i${kk}"></canvas><b>${n}</b><span>${t}</span><em><img class="coin" alt="">${CFG.shop.prices[kk]}</em><i class="cnt"></i></button>`).join('')}</div>
        <p class="shint">Купленное появится на поле следующего уровня в случайных клетках вместо кристаллов.</p>
        <button class="btn go" type="button"><span>В путь</span>${ICON.play}</button>
      </section>
      <section class="scr panel help off" aria-label="Справка">
        <div class="head"><button class="back" type="button" aria-label="Назад">${ICON.back}</button><h2>Как играть</h2></div>
        <div class="scroll" tabindex="0"></div>
      </section>
      <section class="scr panel settings off" aria-label="Настройки">
        <div class="head"><button class="back" type="button" aria-label="Назад">${ICON.back}</button><h2>Настройки</h2></div>
        <div class="opt">
          <h3>Качество графики</h3>
          <div class="seg q" role="group" aria-label="Качество графики"><button type="button" data-q="high">Высокое</button><button type="button" data-q="mid">Среднее</button><button type="button" data-q="low">Низкое</button><button type="button" data-q="pixel">Пиксели</button></div>
          <p>Низкое — меньше частиц и мерцания, для офисных компьютеров. «Пиксели» — шутки ради: всё как в старой приставке.</p>
          <h3>Громкость</h3>
          <label class="vol"><span>Музыка</span><input type="range" class="vm" min="0" max="100" step="5" aria-label="Громкость музыки"><output class="vmo"></output></label>
          <label class="vol"><span>Эффекты</span><input type="range" class="vs" min="0" max="100" step="5" aria-label="Громкость эффектов"><output class="vso"></output></label>
          <p>Звук включается кнопкой с динамиком в заголовке окна.</p>
          <div class="swrow"><span id="rm">Меньше анимации</span><button class="sw rm" type="button" role="switch" aria-checked="false" aria-labelledby="rm"></button></div>
          <p>Без тряски, вспышек и замедлений. Все игровые действия сохраняются.</p>
        </div>
      </section>
      <section class="scr panel records off" aria-label="Рекорды">
        <div class="head"><button class="back" type="button" aria-label="Назад">${ICON.back}</button><h2>Рекорды</h2></div>
        <ol class="recs"></ol>
      </section>
      <div class="fx"></div>
      <div class="sr" aria-live="polite"></div>
    </div>
    <div class="grip" title="Изменить размер"></div>
  </div>`;

  // ---------- Звук: синтез WebAudio, по умолчанию выключен ----------
  // Эффекты низкие и плотные (удары, взрывы, электричество), музыка — спокойный lo-fi без высоких нот.
  function createAudio(vol) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    const ctx = new AC();
    const master = ctx.createGain(); master.gain.value = 0;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18; comp.knee.value = 14; comp.ratio.value = 4; comp.attack.value = 0.004; comp.release.value = 0.3;
    const warm = ctx.createBiquadFilter(); warm.type = 'lowpass'; warm.frequency.value = 7000; // общий срез верха: ничего не пищит
    master.connect(warm); warm.connect(comp); comp.connect(ctx.destination);
    const sfxBus = ctx.createGain(), musicBus = ctx.createGain();
    sfxBus.connect(master); musicBus.connect(master);
    const rev = ctx.createConvolver(), revIn = ctx.createGain(); revIn.gain.value = 0.35;
    const len = Math.floor(ctx.sampleRate * 2.2), ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) { const d = ir.getChannelData(ch); for (let k = 0; k < len; k++) d[k] = (Math.random() * 2 - 1) * Math.pow(1 - k / len, 3); }
    rev.buffer = ir; revIn.connect(rev); rev.connect(sfxBus);
    const nb = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate), nd = nb.getChannelData(0);
    for (let k = 0; k < nd.length; k++) nd[k] = Math.random() * 2 - 1;
    const curve = new Float32Array(1024); for (let k = 0; k < 1024; k++) { const x0 = k / 512 - 1; curve[k] = Math.tanh(x0 * 3.2); }
    function shaper() { const w = ctx.createWaveShaper(); w.curve = curve; w.oversample = '2x'; return w; }
    const out = (node, wet, bus = sfxBus) => { node.connect(bus); if (wet && bus === sfxBus) { const s = ctx.createGain(); s.gain.value = wet; node.connect(s); s.connect(revIn); } };
    // Огибающая с нулевым стартом: без щелчков в начале звука.
    function env(gp, t, a, peak, d) { gp.setValueAtTime(0, t); gp.linearRampToValueAtTime(peak, t + a); gp.exponentialRampToValueAtTime(0.0001, t + a + d); }
    function chain(src, gn, o) {
      let node = src;
      if (o.dist) { const w = shaper(); node.connect(w); node = w; }
      if (o.lp || o.bp) {
        const fl = ctx.createBiquadFilter(); fl.type = o.bp ? 'bandpass' : 'lowpass'; fl.frequency.setValueAtTime(o.lp || o.bp, o.t); fl.Q.value = o.q || 0.8;
        if (o.fTo) fl.frequency.exponentialRampToValueAtTime(o.fTo, o.t + o.d);
        node.connect(fl); node = fl;
      }
      node.connect(gn);
    }
    function tone(type, f, t, d, peak, o = {}) {
      const os = ctx.createOscillator(), gn = ctx.createGain();
      os.type = type; os.frequency.setValueAtTime(f, t);
      if (o.to) os.frequency.exponentialRampToValueAtTime(o.to, t + (o.glide || d));
      if (o.detune) os.detune.value = o.detune;
      if (o.fm) { const m = ctx.createOscillator(), mg = ctx.createGain(); m.type = 'square'; m.frequency.value = o.fm; mg.gain.value = o.fmDepth || 40; m.connect(mg); mg.connect(os.frequency); m.start(t); m.stop(t + d + 0.2); }
      env(gn.gain, t, o.a || 0.004, peak, d);
      chain(os, gn, { ...o, t, d });
      out(gn, o.wet ?? 0.2, o.bus);
      os.start(t); os.stop(t + (o.a || 0.004) + d + 0.1);
      return os;
    }
    function noise(t, d, peak, o = {}) {
      const src = ctx.createBufferSource(), gn = ctx.createGain();
      src.buffer = nb; src.loop = true;
      env(gn.gain, t, o.a || 0.005, peak, d);
      const fl = ctx.createBiquadFilter(); fl.type = o.type || 'bandpass'; fl.frequency.setValueAtTime(o.f || 800, t); fl.Q.value = o.q || 0.9;
      if (o.to) fl.frequency.exponentialRampToValueAtTime(o.to, t + (o.glide || d));
      let node = fl; src.connect(fl);
      if (o.dist) { const w = shaper(); fl.connect(w); node = w; }
      node.connect(gn); out(gn, o.wet ?? 0.25, o.bus);
      src.start(t, Math.random() * 1.5); src.stop(t + (o.a || 0.005) + d + 0.1);
      return gn;
    }
    const hz = (m) => 440 * Math.pow(2, (m - 69) / 12);
    const last = {};
    const gate = (k, gap) => { const n = ctx.currentTime; if (last[k] && n - last[k] < gap) return false; last[k] = n; return true; };
    const now = () => ctx.currentTime + 0.005;
    function pad(notes, t, d, peak, lp = 900, a = 0.12) { for (const m of notes) for (const dt of [-8, 8]) tone('sawtooth', hz(m), t, d, peak, { a, detune: dt, lp, wet: 0.4 }); }
    function boom(t, k, peak) {
      tone('sine', 90 * k, t, 1.0 * k, peak, { to: 26, glide: 0.7 * k, wet: 0.25 });
      noise(t, 1.1 * k, peak * 0.75, { type: 'lowpass', f: 1300, to: 70, q: 0.7, dist: true, wet: 0.45 });
      noise(t, 0.22, peak * 0.4, { f: 650, q: 0.7, wet: 0.2 });
      tone('square', 55, t, 0.3 * k, peak * 0.14, { to: 30, lp: 280 });
    }
    function zap(t, d, peak, f = 90) {
      tone('sawtooth', f, t, d, peak, { fm: 31, fmDepth: f * 0.7, bp: 1300, q: 1.6, dist: true, wet: 0.35 });
      noise(t, d * 0.4, peak * 0.5, { f: 2600, q: 4, wet: 0.2 });
    }
    const S = {
      click() { const t = now(); tone('sine', 320, t, 0.06, 0.12, { to: 230, wet: 0.05 }); noise(t, 0.03, 0.05, { type: 'lowpass', f: 1500 }); },
      select() { const t = now(); tone('triangle', 180, t, 0.09, 0.16, { to: 250, lp: 1200, wet: 0.1 }); noise(t, 0.04, 0.04, { f: 1200, q: 1.5 }); },
      swap() { const t = now(); noise(t, 0.18, 0.14, { f: 380, to: 1400, q: 1, wet: 0.1 }); tone('sine', 110, t, 0.1, 0.1, { to: 150 }); },
      bad() { const t = now(); tone('sine', 120, t, 0.18, 0.32, { to: 68, wet: 0.05 }); noise(t, 0.12, 0.12, { type: 'lowpass', f: 500 }); },
      match(size, wave) {
        if (!gate('match', 0.04)) return;
        const t = now(), PENT = [0, 3, 5, 7, 10, 12, 15, 17];
        noise(t, 0.15, 0.22, { f: 900 + wave * 120, q: 1.6, wet: 0.3 }); // хрустальный хруст
        noise(t, 0.06, 0.05, { f: 3200, q: 0.8 });
        tone('sine', 150 + wave * 18, t, 0.15, 0.3, { to: 85 }); // упругий удар
        tone('triangle', hz(57 + PENT[Math.min(7, wave + (size > 3 ? 1 : 0))]), t, 0.35, 0.05, { lp: 1100, wet: 0.5 });
      },
      shatter() { if (!gate('shatter', 0.05)) return; const t = now(); noise(t, 0.11, 0.1, { f: 1100, q: 1.2, wet: 0.2 }); },
      create() { const t = now(); tone('sawtooth', 70, t, 0.45, 0.12, { to: 210, lp: 900, wet: 0.3 }); tone('sine', 55, t, 0.4, 0.3, { to: 110 }); noise(t, 0.4, 0.08, { f: 300, to: 2000, wet: 0.5 }); },
      rocket() {
        if (!gate('rocket', 0.07)) return;
        const t = now();
        noise(t, 0.55, 0.34, { f: 180, to: 1600, q: 0.8, wet: 0.3 }); // реактивный рёв
        tone('sawtooth', 45, t, 0.5, 0.18, { to: 90, dist: true, lp: 600 });
        for (let k = 0; k < 6; k++) noise(t + Math.random() * 0.3, 0.03, 0.06, { f: 2400, q: 3 }); // треск
      },
      bomb(big) { if (!gate('bomb', 0.09)) return; boom(now(), big ? 1.4 : 1, big ? 0.95 : 0.8); },
      prop() {
        const t = now(), g = noise(t, 0.6, 0.3, { type: 'lowpass', f: 700, wet: 0.25 });
        const lfo = ctx.createOscillator(), lg = ctx.createGain(); lfo.frequency.value = 16; lg.gain.value = 0.22; lfo.connect(lg); lg.connect(g.gain); lfo.start(t); lfo.stop(t + 0.7);
        tone('triangle', 90, t, 0.55, 0.1, { to: 140, lp: 400 });
      },
      beam() { if (!gate('beam', 0.06)) return; zap(now(), 0.22, 0.07); },
      mega() { const t = now(); zap(t, 0.7, 0.13, 55); tone('sine', 45, t, 0.6, 0.3, { to: 70 }); noise(t, 0.6, 0.12, { f: 400, to: 2500, wet: 0.5 }); },
      combo() { const t = now(); tone('sine', 70, t, 0.6, 0.6, { to: 34 }); noise(t, 0.5, 0.4, { type: 'lowpass', f: 2000, to: 200, dist: true, wet: 0.4 }); noise(t, 0.3, 0.1, { f: 200, to: 1500 }); },
      megamega() { const t = now(); noise(t, 0.8, 0.2, { f: 150, to: 3000, q: 0.7, wet: 0.5 }); zap(t + 0.2, 0.6, 0.1, 50); boom(t + 0.75, 1.5, 0.95); },
      ice() { if (!gate('ice', 0.05)) return; const t = now(); noise(t, 0.06, 0.12, { f: 2000, q: 4 }); noise(t, 0.1, 0.08, { f: 900, q: 2, wet: 0.3 }); tone('sine', 260, t, 0.1, 0.06, { to: 180 }); },
      lock() { if (!gate('lock', 0.06)) return; const t = now(); tone('square', 160, t, 0.12, 0.05, { lp: 1400, wet: 0.3 }); tone('square', 241, t, 0.1, 0.04, { lp: 1400 }); noise(t, 0.08, 0.06, { f: 1800, q: 6 }); },
      stone() { if (!gate('stone', 0.06)) return; const t = now(); for (let k = 0; k < 4; k++) noise(t + k * 0.04 + Math.random() * 0.03, 0.08, 0.2, { type: 'lowpass', f: 700 }); tone('sine', 90, t, 0.2, 0.3, { to: 50 }); },
      praise(tier) { const t = now(); pad([[48, 52, 55], [48, 52, 55, 59], [43, 48, 52, 55, 59]][tier - 1], t, 0.9, 0.035, 900, 0.1); noise(t, 0.4, 0.05, { type: 'lowpass', f: 1500, wet: 0.5 }); },
      star(k) { const t = now(); tone('sine', 110, t, 0.35, 0.35, { to: 55 }); tone('triangle', hz([48, 52, 55][k]), t, 0.6, 0.08, { lp: 900, wet: 0.5 }); noise(t, 0.3, 0.04, { type: 'lowpass', f: 3000, wet: 0.6 }); },
      win() { const t = now(); pad([36, 43, 48, 52, 55], t, 1.8, 0.03, 1200, 0.25); tone('sine', 80, t, 0.5, 0.35, { to: 40 }); noise(t, 0.6, 0.08, { f: 200, to: 1800, wet: 0.6 }); },
      lose() { const t = now(); [45, 41, 38].forEach((m, k) => pad([m, m + 3, m + 7], t + k * 0.35, 0.5, 0.025, 700, 0.06)); },
      shuffle() { const t = now(); noise(t, 0.35, 0.15, { f: 200, to: 1200, q: 1.2 }); noise(t + 0.35, 0.35, 0.15, { f: 1200, to: 200, q: 1.2 }); },
      tick() { if (!gate('tick', 0.04)) return; const t = now(); tone('sine', 140, t, 0.08, 0.18, { to: 90 }); noise(t, 0.03, 0.05, { type: 'lowpass', f: 1000 }); },
    };

    // ----- Музыка: lo-fi / синтвейв, 82 удара в минуту, всё ниже ~400 Гц, кроме мягкой перкуссии -----
    const BPM = 82, STEP = 60 / BPM / 4; // шестнадцатая
    const PROG = [ // [бас, аккорд] — две четвёрки аккордов в до мажоре
      [[38, [53, 57, 60, 64]], [43, [53, 57, 59, 64]], [36, [52, 55, 59, 62]], [33, [55, 59, 60, 64]]], // Dm9 G13 Cmaj9 Am9
      [[41, [53, 57, 60, 64]], [40, [50, 55, 59, 62]], [38, [53, 57, 60, 62]], [43, [53, 55, 60, 62]]], // Fmaj7 Em7 Dm7 G7sus
    ];
    const KICK = [1, 0, 0, 0, 0, 0, 0, 0.55, 0, 0, 1, 0, 0, 0, 0, 0];
    const SNARE = [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0.25];
    const BASS = [[0, 0, 3], [6, 0, 1.5], [10, 7, 2], [14, -2, 1]]; // [шаг, интервал, длина в шестнадцатых]
    const KEYS = [[2, 0], [5, 1], [8, 2], [11, 3], [13, 1]]; // [шаг, номер ноты аккорда]
    const padBus = ctx.createGain(); padBus.gain.value = 1; padBus.connect(musicBus);
    const wob = ctx.createOscillator(), wobG = ctx.createGain(); wob.frequency.value = 0.35; wobG.gain.value = 6; wob.connect(wobG); wob.start(); // «плёночное» плавание тона
    const vinyl = ctx.createBufferSource(), vf = ctx.createBiquadFilter(), vg = ctx.createGain();
    vinyl.buffer = nb; vinyl.loop = true; vf.type = 'lowpass'; vf.frequency.value = 2200; vg.gain.value = 0.006;
    vinyl.connect(vf); vf.connect(vg); vg.connect(musicBus); vinyl.start();
    const mtone = (type, f, t, d, peak, o = {}) => { const os = tone(type, f, t, d, peak, { ...o, bus: musicBus, wet: 0 }); wobG.connect(os.detune); return os; };
    let step = 0, nextT = 0, playing = false;
    function schedule(s, t) {
      const bar = Math.floor(s / 16) % 8, st = s % 16, [bass, chord] = PROG[bar >> 2][bar % 4];
      const sw = st % 2 ? STEP * 0.18 : 0; // свинг
      t += sw;
      if (KICK[st]) {
        const v = KICK[st];
        tone('sine', 110, t, 0.28, 0.3 * v, { to: 45, glide: 0.1, bus: musicBus, wet: 0 });
        padBus.gain.cancelScheduledValues(t); padBus.gain.setValueAtTime(0.4, t); padBus.gain.linearRampToValueAtTime(1, t + 0.28); // мягкая «накачка»
      }
      if (SNARE[st]) { noise(t, 0.16, 0.12 * SNARE[st], { f: 1500, q: 0.8, bus: musicBus, wet: 0 }); tone('sine', 190, t, 0.08, 0.08 * SNARE[st], { to: 150, bus: musicBus, wet: 0 }); }
      if (st % 2 === 0) noise(t, 0.04, st % 4 ? 0.012 : 0.018, { f: 3800, q: 1.2, bus: musicBus, wet: 0 }); // тихий шейкер
      for (const [bs, iv, l] of BASS) if (bs === st && !(iv === -2 && bar % 2 === 0)) mtone('triangle', hz(bass + iv), t, STEP * l, 0.13, { lp: 300 });
      if (st === 0) for (const m of chord) for (const dt of [-7, 7]) { const os = tone('sawtooth', hz(m), t, STEP * 16 * 0.95, 0.03, { a: 0.6, detune: dt, lp: 1100, bus: padBus, wet: 0 }); wobG.connect(os.detune); }
      for (const [ks, ni] of KEYS) if (ks === st && (bar % 2 || ks !== 13)) mtone('triangle', hz(chord[ni]), t, STEP * 5, 0.09, { a: 0.008, lp: 1500 });
      if (Math.random() < 0.12) noise(t + Math.random() * STEP, 0.006, 0.05, { type: 'lowpass', f: 3000, bus: musicBus, wet: 0 }); // потрескивание пластинки
    }
    let enabled = false;
    const api = {
      ctx, S,
      enable(on) {
        enabled = on;
        const t = ctx.currentTime;
        if (on && ctx.state !== 'running') ctx.resume();
        master.gain.cancelScheduledValues(t); master.gain.setValueAtTime(master.gain.value, t); master.gain.linearRampToValueAtTime(on ? 0.75 : 0, t + 0.12);
      },
      volumes(music, effects) {
        const t = ctx.currentTime;
        musicBus.gain.setTargetAtTime(music * 0.7, t, 0.05);
        sfxBus.gain.setTargetAtTime(effects, t, 0.05);
      },
      // Вызывается из кадра игры: досылает музыку на 0,35 с вперёд. Окно скрыто — кадров нет, музыка затихает.
      tick() {
        if (!enabled || musicBus.gain.value < 0.001) { playing = false; return; }
        const t = ctx.currentTime;
        if (!playing || nextT < t - 0.2) { nextT = t + 0.08; playing = true; }
        while (nextT < t + 0.35) { schedule(step, nextT); step++; nextT += STEP; }
      },
      close() { try { ctx.close(); } catch (e) { /* уже закрыт */ } },
    };
    api.volumes(vol.music, vol.sfx);
    return api;
  }

  // ---------- Окно игры (создаётся на каждое открытие, уничтожается целиком при закрытии) ----------
  let inst = null, frames = 0;

  function createInstance() {
    const ac = new AbortController(), sig = { signal: ac.signal };
    const on = (el, ev, fn, opt) => el.addEventListener(ev, fn, Object.assign({}, sig, opt));
    const capture = (el, e) => { try { el.setPointerCapture(e.pointerId); } catch (err) { /* указатель уже отпущен */ } };
    const host = document.createElement('div');
    host.setAttribute('data-crystals', '');
    const shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = HTML;
    // Конструируемая таблица стилей: страница работает с CSP без 'unsafe-inline', обычный <style> там был бы заблокирован
    try { const sh = new CSSStyleSheet(); sh.replaceSync(CSS); shadow.adoptedStyleSheets = [sh]; }
    catch (err) { const st = document.createElement('style'); st.textContent = CSS; shadow.prepend(st); }
    document.body.appendChild(host);
    const $ = (s) => shadow.querySelector(s), $$ = (s) => [...shadow.querySelectorAll(s)];
    const win = $('.win'), bar = $('.bar'), cv = $('canvas.main');
    let x = cv.getContext('2d');
    const fxLayer = $('.fx'), live = $('.sr');

    // Настройки v2: по умолчанию низкое качество (офисные компьютеры), музыка и эффекты — отдельные громкости
    const settings = Object.assign({ quality: 'low', reduce: matchMedia('(prefers-reduced-motion: reduce)').matches, music: 0.5, sfx: 0.8 }, store.get('settings.v2', {}));
    const saveSettings = () => store.set('settings.v2', settings);
    const PIXEL = 0.36; // «пиксель-арт»: холст в ~3 раза грубее и растягивается без сглаживания
    let destroyed = false, minimized = false, raf = 0, lastFrame = 0;
    let u = 1, dpr = 1, k = 1, wx = 0, wy = 0;
    let audio = null, soundOn = false;
    let sprites = null, spritesDirty = true, icons = null, dragGrip = null;
    const sfx = (name, ...a) => { if (soundOn && audio) audio.S[name](...a); };

    // ----- Размер и положение окна -----
    function fitU(want) {
      const maxU = Math.min((innerWidth - 16) / W0, (innerHeight - 16) / (H0 + BAR));
      return clamp(want, Math.min(MIN_U, maxU), Math.min(MAX_U, maxU));
    }
    function applyGeometry(want, nx, ny) {
      u = fitU(want);
      const w = W0 * u, h = (minimized ? BAR : H0 + BAR) * u;
      wx = clamp(nx, 8, Math.max(8, innerWidth - w - 8));
      wy = clamp(ny, 8, Math.max(8, innerHeight - h - 8));
      win.style.setProperty('--u', u + 'px');
      win.style.setProperty('--x', Math.round(wx) + 'px');
      win.style.setProperty('--y', Math.round(wy) + 'px');
      resizeCanvas();
    }
    function resizeCanvas() {
      dpr = Math.min(3, window.devicePixelRatio || 1);
      const pixel = settings.quality === 'pixel', nd = pixel ? PIXEL : dpr;
      const pw = Math.round(W0 * u * nd), ph = Math.round(H0 * u * nd);
      if (cv.width !== pw || cv.height !== ph) { cv.width = pw; cv.height = ph; }
      cv.classList.toggle('pixel', pixel);
      const nk = u * nd;
      if (Math.abs(nk - k) > 0.001 || !sprites) { k = nk; spritesDirty = true; }
    }
    const saved = store.get('win', null);
    const u0 = saved ? saved.u : Math.min(1, fitU(1));
    let wantU = u0;
    applyGeometry(u0, saved ? saved.x : innerWidth - W0 * fitU(u0) - 28, saved ? saved.y : (innerHeight - (H0 + BAR) * fitU(u0)) / 2);
    const saveWin = () => store.set('win', { x: Math.round(wx), y: Math.round(wy), u: +u.toFixed(3) });

    // Перетаскивание за заголовок
    let dragWin = null;
    on(bar, 'pointerdown', (e) => {
      if (e.button !== 0 || e.target.closest('button')) return;
      dragWin = { id: e.pointerId, dx: e.clientX - wx, dy: e.clientY - wy };
      capture(bar, e); bar.classList.add('drag'); e.preventDefault();
    });
    on(bar, 'pointermove', (e) => { if (dragWin && e.pointerId === dragWin.id) applyGeometry(u, e.clientX - dragWin.dx, e.clientY - dragWin.dy); });
    const endDrag = (e) => { if (dragWin && e.pointerId === dragWin.id) { dragWin = null; bar.classList.remove('drag'); saveWin(); } };
    on(bar, 'pointerup', endDrag); on(bar, 'pointercancel', endDrag);
    on(bar, 'dblclick', (e) => { if (!e.target.closest('button')) toggleMin(); });

    // Изменение размера за уголок — пропорционально, чтобы поле оставалось квадратным
    const grip = $('.grip');
    on(grip, 'pointerdown', (e) => { if (e.button !== 0) return; dragGrip = { id: e.pointerId, x0: e.clientX, y0: e.clientY, u0: u }; capture(grip, e); e.preventDefault(); e.stopPropagation(); });
    on(grip, 'pointermove', (e) => {
      if (!dragGrip || e.pointerId !== dragGrip.id) return;
      const d = Math.max((e.clientX - dragGrip.x0) / W0, (e.clientY - dragGrip.y0) / (H0 + BAR));
      applyGeometry(dragGrip.u0 + d, wx, wy);
    });
    const endGrip = (e) => { if (dragGrip && e.pointerId === dragGrip.id) { dragGrip = null; wantU = u; saveWin(); } };
    on(grip, 'pointerup', endGrip); on(grip, 'pointercancel', endGrip);
    on(window, 'resize', () => applyGeometry(wantU, wx, wy)); // окно вернётся к выбранному размеру, когда место появится

    function toggleMin() {
      minimized = !minimized;
      win.classList.toggle('min', minimized);
      $('.mn').setAttribute('aria-label', minimized ? 'Развернуть' : 'Свернуть');
      $('.mn').title = minimized ? 'Развернуть' : 'Свернуть';
      applyGeometry(u, wx, wy);
      if (!minimized) wake();
    }
    on($('.mn'), 'click', toggleMin);
    on($('.cls'), 'click', () => destroy());
    on($('.snd'), 'click', () => {
      soundOn = !soundOn;
      if (soundOn && !audio) audio = createAudio(settings);
      if (audio) audio.enable(soundOn);
      const b = $('.snd');
      b.classList.toggle('on', soundOn); b.setAttribute('aria-pressed', String(soundOn));
      b.setAttribute('aria-label', soundOn ? 'Выключить звук' : 'Включить звук');
      b.innerHTML = soundOn ? ICON.sndOn : ICON.sndOff;
      if (soundOn) sfx('click');
    });

    // ----- Спрайты: рисуются программно один раз под текущий масштаб -----
    function mk(w, h) { const c = document.createElement('canvas'); c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h)); return c; }
    function poly(g, pts, R) { g.beginPath(); pts.forEach(([px, py], i) => (i ? g.lineTo(px * R, py * R) : g.moveTo(px * R, py * R))); g.closePath(); }
    function shade(p, s) { const t = (clamp(s, -1, 1) + 1) / 2; return t < 0.5 ? mix(p.dark, p.base, t * 2) : mix(p.base, p.light, (t - 0.5) * 2); }
    function star4(g, cx, cy, r, a) {
      g.save(); g.translate(cx, cy); g.globalAlpha *= a; g.fillStyle = '#fff'; g.beginPath();
      for (let q = 0; q < 8; q++) { const rr = q % 2 ? r * 0.18 : r, an = q * Math.PI / 4; g.lineTo(Math.cos(an) * rr, Math.sin(an) * rr); }
      g.closePath(); g.fill(); g.restore();
    }
    // Огранка в два яруса (павильон → корона → площадка), преломление внутри площадки,
    // рефлекс снизу справа, блики на гранях, обращённых к свету, и светящееся ядро.
    function drawGem(g, shape, p, R) {
      const pts = SHAPES[shape], n = pts.length, tk = TABLE_K[shape], mk2 = (tk + 1) / 2;
      const tab = pts.map(([px, py]) => [px * tk, py * tk - 0.05]);
      const mid = pts.map(([px, py]) => [px * mk2, py * mk2 - 0.025]);
      const L = [-0.55, -0.83];
      const lit = (a, b) => { const M = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], l = Math.hypot(M[0], M[1]) || 1; return (M[0] / l) * L[0] + (M[1] / l) * L[1]; };
      const tri = (a, b, c, col) => { g.beginPath(); g.moveTo(a[0] * R, a[1] * R); g.lineTo(b[0] * R, b[1] * R); g.lineTo(c[0] * R, c[1] * R); g.closePath(); g.fillStyle = col; g.strokeStyle = col; g.lineWidth = 0.7; g.fill(); g.stroke(); };
      // силуэт и цветное свечение
      g.save(); g.shadowColor = p.glow; g.shadowBlur = R * 0.55; poly(g, pts, R); g.fillStyle = p.dark; g.fill(); g.restore();
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n, P = pts[i], P2 = pts[j], M1 = mid[i], M2 = mid[j], Q = tab[i], Q2 = tab[j];
        const s = lit(P, P2), E = [(P[0] + P2[0]) / 2, (P[1] + P2[1]) / 2], EM = [(M1[0] + M2[0]) / 2, (M1[1] + M2[1]) / 2];
        // нижний ярус — темнее, с рефлексом на теневой стороне
        const refl = s < -0.35 ? 0.22 : 0;
        tri(P, E, M1, shade(p, s * 0.9 - 0.15 + refl)); tri(E, P2, M2, shade(p, s * 0.9 - 0.32 + refl)); tri(M1, E, M2, shade(p, s * 0.7 - 0.05 + refl));
        // верхний ярус — ярче, «звёздные» грани
        tri(M1, EM, Q, shade(p, s + 0.28)); tri(EM, M2, Q2, shade(p, s + 0.02)); tri(Q, EM, Q2, shade(p, s * 0.5 + 0.2));
        if (s > 0.55) { g.globalAlpha = (s - 0.55) * 1.4; tri(M1, EM, Q, '#ffffff'); g.globalAlpha = 1; } // блик на гранях к свету
      }
      // площадка: свет из глубины и преломлённая «звезда» павильона
      const gr = g.createRadialGradient(-R * 0.12, -R * 0.2, R * 0.05, 0, 0, R * tk * 1.1);
      gr.addColorStop(0, p.light); gr.addColorStop(0.55, p.base); gr.addColorStop(1, mix(p.base, p.dark, 0.3));
      poly(g, tab, R); g.fillStyle = gr; g.fill();
      g.save(); poly(g, tab, R); g.clip();
      const cx0 = 0.04, cy0 = 0.06;
      for (let i = 0; i < n; i++) {
        const A = tab[i], B = tab[(i + 1) % n];
        g.beginPath(); g.moveTo(cx0 * R, cy0 * R); g.lineTo(A[0] * R, A[1] * R); g.lineTo(B[0] * R, B[1] * R); g.closePath();
        g.fillStyle = i % 2 ? `rgba(255,255,255,${0.1 + 0.08 * Math.max(0, -lit(A, B))})` : 'rgba(0,0,30,.14)'; g.fill();
      }
      const core = g.createRadialGradient(cx0 * R, cy0 * R, 0, cx0 * R, cy0 * R, R * tk * 0.7);
      core.addColorStop(0, 'rgba(255,255,255,.45)'); core.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = core; g.fillRect(-R, -R, 2 * R, 2 * R);
      g.restore();
      poly(g, tab, R); g.strokeStyle = 'rgba(255,255,255,.45)'; g.lineWidth = R * 0.028; g.stroke();
      poly(g, mid, R); g.strokeStyle = 'rgba(255,255,255,.12)'; g.lineWidth = R * 0.018; g.stroke();
      // контур: тёмный край и тонкая светлая кромка сверху
      poly(g, pts, R); g.strokeStyle = p.dark; g.lineWidth = R * 0.07; g.lineJoin = 'round'; g.stroke();
      g.save(); poly(g, pts, R); g.clip();
      g.strokeStyle = 'rgba(255,255,255,.35)'; g.lineWidth = R * 0.05; g.translate(R * 0.02, R * 0.03); poly(g, pts, R); g.stroke();
      g.restore();
      g.save(); poly(g, pts, R); g.clip();
      const hg = g.createRadialGradient(-R * 0.38, -R * 0.52, 0, -R * 0.38, -R * 0.52, R * 0.5);
      hg.addColorStop(0, 'rgba(255,255,255,.7)'); hg.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = hg; g.fillRect(-R, -R, R * 2, R * 2);
      const rg = g.createRadialGradient(R * 0.45, R * 0.55, 0, R * 0.45, R * 0.55, R * 0.55); // тёплый рефлекс снизу
      rg.addColorStop(0, 'rgba(255,255,255,.18)'); rg.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = rg; g.fillRect(-R, -R, R * 2, R * 2);
      g.restore();
      star4(g, -R * 0.32, -R * 0.44, R * 0.28, 0.95);
      star4(g, R * 0.28, R * 0.18, R * 0.12, 0.55);
    }
    function gemSprite(shape, p, S) {
      const c = mk(S, S), g = c.getContext('2d');
      g.translate(S / 2, S / 2); drawGem(g, shape, p, S / SPR * 0.43);
      return c;
    }
    function shineFrames(shape, S) {
      const out = [];
      for (let f = 0; f < 10; f++) {
        const c = mk(S, S), g = c.getContext('2d'), R = S / SPR * 0.43;
        g.translate(S / 2, S / 2); poly(g, SHAPES[shape], R); g.fillStyle = '#fff'; g.fill();
        g.globalCompositeOperation = 'source-in'; g.rotate(-0.65);
        const pos = (-1.5 + 3 * f / 9) * R, gr = g.createLinearGradient(pos - R * 0.4, 0, pos + R * 0.4, 0);
        gr.addColorStop(0, 'rgba(255,255,255,0)'); gr.addColorStop(0.5, 'rgba(255,255,255,.8)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
        g.fillStyle = gr; g.fillRect(-R * 2, -R * 2, R * 4, R * 4);
        out.push(c);
      }
      return out;
    }
    function rocketSprite(p, S) {
      const c = mk(S, S), g = c.getContext('2d'), R = S / SPR * 0.45;
      g.translate(S / 2, S / 2);
      g.save(); g.shadowColor = p.glow; g.shadowBlur = R * 0.45;
      // хвостовые стабилизаторы
      g.fillStyle = p.dark;
      g.beginPath(); g.moveTo(-0.45 * R, -0.18 * R); g.lineTo(-0.85 * R, -0.52 * R); g.lineTo(-0.78 * R, -0.12 * R); g.closePath(); g.fill();
      g.beginPath(); g.moveTo(-0.45 * R, 0.18 * R); g.lineTo(-0.85 * R, 0.52 * R); g.lineTo(-0.78 * R, 0.12 * R); g.closePath(); g.fill();
      // корпус
      const bg = g.createLinearGradient(0, -0.26 * R, 0, 0.26 * R);
      bg.addColorStop(0, '#ffffff'); bg.addColorStop(0.5, '#dfe5f5'); bg.addColorStop(1, '#8b95bd');
      g.beginPath(); g.moveTo(-0.7 * R, -0.25 * R); g.lineTo(0.4 * R, -0.25 * R); g.quadraticCurveTo(0.95 * R, -0.12 * R, 1.0 * R, 0); g.quadraticCurveTo(0.95 * R, 0.12 * R, 0.4 * R, 0.25 * R); g.lineTo(-0.7 * R, 0.25 * R); g.closePath();
      g.fillStyle = bg; g.fill(); g.restore();
      // нос и полосы цвета кристалла
      g.save(); g.clip();
      const ng = g.createLinearGradient(0, -0.26 * R, 0, 0.26 * R); ng.addColorStop(0, p.light); ng.addColorStop(0.5, p.base); ng.addColorStop(1, p.dark);
      g.fillStyle = ng; g.fillRect(0.45 * R, -R, R, 2 * R); g.fillRect(-0.32 * R, -R, 0.16 * R, 2 * R); g.fillRect(-0.62 * R, -R, 0.12 * R, 2 * R);
      g.restore();
      g.beginPath(); g.moveTo(-0.7 * R, -0.25 * R); g.lineTo(0.4 * R, -0.25 * R); g.quadraticCurveTo(0.95 * R, -0.12 * R, 1.0 * R, 0); g.quadraticCurveTo(0.95 * R, 0.12 * R, 0.4 * R, 0.25 * R); g.lineTo(-0.7 * R, 0.25 * R); g.closePath();
      g.strokeStyle = 'rgba(30,20,70,.75)'; g.lineWidth = R * 0.05; g.stroke();
      // иллюминатор
      g.beginPath(); g.arc(0.14 * R, 0, 0.12 * R, 0, Math.PI * 2);
      const wg = g.createRadialGradient(0.1 * R, -0.04 * R, 0, 0.14 * R, 0, 0.12 * R); wg.addColorStop(0, '#e9fbff'); wg.addColorStop(1, '#2a6fd6');
      g.fillStyle = wg; g.fill(); g.strokeStyle = '#5c6790'; g.lineWidth = R * 0.04; g.stroke();
      // сопло
      g.fillStyle = '#4b4f6a'; g.fillRect(-0.82 * R, -0.16 * R, 0.13 * R, 0.32 * R);
      g.fillStyle = 'rgba(255,255,255,.55)'; g.fillRect(-0.6 * R, -0.2 * R, 0.95 * R, 0.05 * R);
      return c;
    }
    function propBlades(p, S) {
      const c = mk(S, S), g = c.getContext('2d'), R = S / SPR * 0.46;
      g.translate(S / 2, S / 2);
      g.shadowColor = p.glow; g.shadowBlur = R * 0.35;
      for (let q = 0; q < 4; q++) {
        g.save(); g.rotate(q * Math.PI / 2 + Math.PI / 4);
        const bg = g.createLinearGradient(-0.3 * R, 0, 0.3 * R, 0); bg.addColorStop(0, p.light); bg.addColorStop(0.5, p.base); bg.addColorStop(1, p.dark);
        g.beginPath(); g.moveTo(0, -0.1 * R); g.bezierCurveTo(0.42 * R, -0.35 * R, 0.36 * R, -0.95 * R, 0, -0.98 * R); g.bezierCurveTo(-0.3 * R, -0.95 * R, -0.25 * R, -0.35 * R, 0, -0.1 * R);
        g.fillStyle = bg; g.fill(); g.shadowBlur = 0; g.strokeStyle = p.dark; g.lineWidth = R * 0.05; g.stroke();
        g.beginPath(); g.moveTo(-0.05 * R, -0.3 * R); g.quadraticCurveTo(-0.12 * R, -0.65 * R, -0.04 * R, -0.85 * R); g.strokeStyle = 'rgba(255,255,255,.6)'; g.lineWidth = R * 0.06; g.lineCap = 'round'; g.stroke();
        g.restore();
      }
      return c;
    }
    function hubSprite(S) {
      const c = mk(S, S), g = c.getContext('2d'), R = S / SPR * 0.46;
      g.translate(S / 2, S / 2);
      const hg = g.createRadialGradient(-0.06 * R, -0.08 * R, 0, 0, 0, 0.24 * R); hg.addColorStop(0, '#fff7c0'); hg.addColorStop(0.6, '#ffc61f'); hg.addColorStop(1, '#a65a00');
      g.beginPath(); g.arc(0, 0, 0.22 * R, 0, Math.PI * 2); g.fillStyle = hg; g.fill(); g.strokeStyle = '#7a3b00'; g.lineWidth = R * 0.04; g.stroke();
      return c;
    }
    function bombSprite(p, S) {
      const c = mk(S, S), g = c.getContext('2d'), R = S / SPR * 0.44;
      g.translate(S / 2, S / 2 + 0.06 * R);
      g.save(); g.shadowColor = p.glow; g.shadowBlur = R * 0.55;
      const bg = g.createRadialGradient(-0.25 * R, -0.3 * R, 0.05 * R, 0, 0, 0.78 * R);
      bg.addColorStop(0, '#7b7fc0'); bg.addColorStop(0.35, '#2c2766'); bg.addColorStop(1, '#07051a');
      g.beginPath(); g.arc(0, 0.04 * R, 0.72 * R, 0, Math.PI * 2); g.fillStyle = bg; g.fill(); g.restore();
      g.beginPath(); g.arc(0, 0.04 * R, 0.66 * R, 0.1 * Math.PI, 0.75 * Math.PI); g.strokeStyle = p.glow; g.lineWidth = R * 0.08; g.globalAlpha = 0.85; g.stroke(); g.globalAlpha = 1;
      // цветная вставка — по ней видно цвет бомбы
      g.save(); g.translate(0.02 * R, 0.12 * R); drawGem(g, PAL.indexOf(p), p, 0.27 * R); g.restore();
      g.beginPath(); g.ellipse(-0.3 * R, -0.33 * R, 0.2 * R, 0.11 * R, -0.7, 0, Math.PI * 2); g.fillStyle = 'rgba(255,255,255,.55)'; g.fill();
      g.save(); g.translate(0.38 * R, -0.55 * R); g.rotate(0.7);
      g.fillStyle = '#8e93b5'; g.fillRect(-0.14 * R, -0.1 * R, 0.28 * R, 0.2 * R); g.fillStyle = '#c8cce4'; g.fillRect(-0.14 * R, -0.1 * R, 0.28 * R, 0.05 * R); g.restore();
      g.beginPath(); g.moveTo(0.46 * R, -0.66 * R); g.quadraticCurveTo(0.7 * R, -0.95 * R, 0.62 * R, -1.08 * R); g.strokeStyle = '#e9d3a0'; g.lineWidth = R * 0.07; g.lineCap = 'round'; g.stroke();
      return c;
    }
    function megaFacets(S) {
      const c = mk(S, S), g = c.getContext('2d'), R = S / SPR * 0.42;
      g.translate(S / 2, S / 2);
      const ring = circ(10, R, 0), inner = circ(5, R * 0.48, Math.PI / 10);
      for (let i = 0; i < 10; i++) {
        const a = ring[i], b = ring[(i + 1) % 10], m = inner[Math.floor(i / 2)];
        g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.lineTo(m[0], m[1]); g.closePath();
        g.fillStyle = i % 2 ? 'rgba(255,255,255,.22)' : 'rgba(20,0,60,.18)'; g.fill(); g.strokeStyle = 'rgba(255,255,255,.35)'; g.lineWidth = R * 0.025; g.stroke();
      }
      g.beginPath(); inner.forEach(([a, b], i) => (i ? g.lineTo(a, b) : g.moveTo(a, b))); g.closePath(); g.fillStyle = 'rgba(255,255,255,.28)'; g.fill(); g.stroke();
      return c;
    }
    function glowSprite(col) {
      const c = mk(64, 64), g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.22, col); gr.addColorStop(0.55, col.replace('rgb', 'rgba').replace(')', ',.25)')); gr.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
      return c;
    }
    function rr(g, x0, y0, w, h, r) { g.beginPath(); g.roundRect ? g.roundRect(x0, y0, w, h, r) : g.rect(x0, y0, w, h); }
    function iceSprite(lv, S) {
      const c = mk(S, S), g = c.getContext('2d'), m = S * 0.05, w = S - m * 2;
      rr(g, m, m, w, w, S * 0.16);
      const gr = g.createLinearGradient(0, m, 0, S - m);
      gr.addColorStop(0, lv > 1 ? 'rgba(225,248,255,.78)' : 'rgba(200,238,255,.5)'); gr.addColorStop(1, lv > 1 ? 'rgba(120,190,255,.62)' : 'rgba(110,180,255,.32)');
      g.fillStyle = gr; g.fill();
      g.strokeStyle = lv > 1 ? 'rgba(240,252,255,.95)' : 'rgba(220,246,255,.75)'; g.lineWidth = S * (lv > 1 ? 0.05 : 0.03); g.stroke();
      g.save(); g.clip();
      g.fillStyle = 'rgba(255,255,255,.35)'; g.beginPath(); g.moveTo(m, S * 0.42); g.lineTo(S * 0.42, m); g.lineTo(S * 0.58, m); g.lineTo(m, S * 0.58); g.closePath(); g.fill();
      g.strokeStyle = 'rgba(255,255,255,.55)'; g.lineWidth = S * 0.015;
      const cr = lv > 1 ? [[0.2, 0.75, 0.45, 0.6, 0.55, 0.85], [0.7, 0.2, 0.62, 0.42, 0.85, 0.5], [0.3, 0.3, 0.42, 0.45]] : [[0.65, 0.7, 0.78, 0.86]];
      for (const l of cr) { g.beginPath(); g.moveTo(l[0] * S, l[1] * S); for (let q = 2; q < l.length; q += 2) g.lineTo(l[q] * S, l[q + 1] * S); g.stroke(); }
      if (lv > 1) for (let q = 0; q < 14; q++) { g.fillStyle = 'rgba(255,255,255,.5)'; g.beginPath(); g.arc(rnd(0.1, 0.9) * S, rnd(0.1, 0.9) * S, S * rnd(0.008, 0.02), 0, Math.PI * 2); g.fill(); }
      g.restore();
      return c;
    }
    function lockSprite(S) {
      const c = mk(S, S), g = c.getContext('2d');
      const link = (cx, cy, ang) => { g.save(); g.translate(cx, cy); g.rotate(ang); g.beginPath(); g.ellipse(0, 0, S * 0.075, S * 0.045, 0, 0, Math.PI * 2);
        g.strokeStyle = '#4b4f68'; g.lineWidth = S * 0.04; g.stroke(); g.strokeStyle = '#d9deef'; g.lineWidth = S * 0.022; g.stroke(); g.restore(); };
      for (const dir of [1, -1]) for (let q = 0; q <= 6; q++) { const t = q / 6; link(S * (0.08 + 0.84 * t), S * (dir > 0 ? 0.08 + 0.84 * t : 0.92 - 0.84 * t), dir * Math.PI / 4 + (q % 2 ? Math.PI / 2 : 0)); }
      g.save(); g.translate(S * 0.5, S * 0.56);
      g.beginPath(); g.arc(0, -S * 0.07, S * 0.075, Math.PI, 0); g.strokeStyle = '#8d6a1e'; g.lineWidth = S * 0.04; g.stroke();
      const lg = g.createLinearGradient(0, -S * 0.06, 0, S * 0.12); lg.addColorStop(0, '#ffe27a'); lg.addColorStop(1, '#c47d00');
      rr(g, -S * 0.11, -S * 0.07, S * 0.22, S * 0.18, S * 0.04); g.fillStyle = lg; g.fill(); g.strokeStyle = '#7a4a00'; g.lineWidth = S * 0.02; g.stroke();
      g.fillStyle = '#5a3400'; g.beginPath(); g.arc(0, 0, S * 0.025, 0, Math.PI * 2); g.fill(); g.fillRect(-S * 0.01, 0, S * 0.02, S * 0.06);
      g.restore();
      return c;
    }
    function stoneSprite(lv, S) {
      const c = mk(S, S), g = c.getContext('2d'), R = S * 0.46;
      g.translate(S / 2, S / 2);
      const pts = [[-0.95, -0.55], [-0.55, -0.95], [0.5, -0.92], [0.95, -0.5], [0.92, 0.55], [0.55, 0.95], [-0.5, 0.92], [-0.93, 0.5]];
      const base = { base: lv > 1 ? '#6b5a9c' : '#7d6ca8', light: '#c9bdf0', dark: '#2c2150' };
      g.save(); g.shadowColor = 'rgba(0,0,0,.55)'; g.shadowBlur = R * 0.2; g.shadowOffsetY = R * 0.08; poly(g, pts, R); g.fillStyle = base.dark; g.fill(); g.restore();
      const tab = pts.map(([a, b]) => [a * 0.62, b * 0.62 - 0.04]);
      for (let i = 0; i < 8; i++) {
        const P = pts[i], P2 = pts[(i + 1) % 8], Q = tab[i], Q2 = tab[(i + 1) % 8], M = [(P[0] + P2[0]) / 2, (P[1] + P2[1]) / 2], l = Math.hypot(M[0], M[1]);
        g.beginPath(); g.moveTo(P[0] * R, P[1] * R); g.lineTo(P2[0] * R, P2[1] * R); g.lineTo(Q2[0] * R, Q2[1] * R); g.lineTo(Q[0] * R, Q[1] * R); g.closePath();
        g.fillStyle = shade(base, (M[0] / l) * -0.55 + (M[1] / l) * -0.83); g.fill();
      }
      poly(g, tab, R); const tg = g.createLinearGradient(-R * 0.5, -R * 0.5, R * 0.5, R * 0.5); tg.addColorStop(0, '#a796d6'); tg.addColorStop(1, base.base); g.fillStyle = tg; g.fill();
      poly(g, pts, R); g.strokeStyle = '#1f1640'; g.lineWidth = R * 0.06; g.stroke();
      if (lv > 1) { // металлические скобы — крепкий камень
        g.strokeStyle = '#c9cfe6'; g.lineWidth = R * 0.14; g.beginPath(); g.moveTo(-0.62 * R, -0.62 * R); g.lineTo(0.62 * R, 0.62 * R); g.moveTo(0.62 * R, -0.62 * R); g.lineTo(-0.62 * R, 0.62 * R); g.stroke();
        g.strokeStyle = '#5c6283'; g.lineWidth = R * 0.04; g.stroke();
        for (const [a, b] of [[-0.62, -0.62], [0.62, 0.62], [0.62, -0.62], [-0.62, 0.62]]) { g.beginPath(); g.arc(a * R, b * R, R * 0.07, 0, Math.PI * 2); g.fillStyle = '#eef1fb'; g.fill(); }
      } else {
        g.strokeStyle = 'rgba(20,10,40,.8)'; g.lineWidth = R * 0.05; g.beginPath(); g.moveTo(-0.1 * R, -0.85 * R); g.lineTo(0.05 * R, -0.3 * R); g.lineTo(-0.2 * R, 0.1 * R); g.lineTo(0.15 * R, 0.5 * R); g.moveTo(0.05 * R, -0.3 * R); g.lineTo(0.5 * R, -0.15 * R); g.stroke();
      }
      g.beginPath(); g.ellipse(-0.35 * R, -0.45 * R, 0.25 * R, 0.1 * R, -0.6, 0, Math.PI * 2); g.fillStyle = 'rgba(255,255,255,.25)'; g.fill();
      return c;
    }
    function boardPanel() {
      const c = mk(W0 * k, H0 * k), g = c.getContext('2d');
      g.scale(k, k);
      g.save(); g.shadowColor = 'rgba(150,90,255,.55)'; g.shadowBlur = 22;
      rr(g, BX - 7, BY - 7, BS + 14, BS + 14, 18); g.fillStyle = 'rgba(14,8,42,.86)'; g.fill(); g.restore();
      rr(g, BX - 7, BY - 7, BS + 14, BS + 14, 18);
      const bg = g.createLinearGradient(0, BY, 0, BY + BS); bg.addColorStop(0, 'rgba(200,170,255,.7)'); bg.addColorStop(1, 'rgba(110,80,220,.5)');
      g.strokeStyle = bg; g.lineWidth = 1.6; g.stroke();
      for (let r = 0; r < NN; r++) for (let cc = 0; cc < NN; cc++) {
        const x0 = BX + cc * CS + 1.5, y0 = BY + r * CS + 1.5, w = CS - 3;
        rr(g, x0, y0, w, w, 9);
        const cg = g.createLinearGradient(0, y0, 0, y0 + w);
        const a = (r + cc) % 2 ? 0.075 : 0.045;
        cg.addColorStop(0, `rgba(120,100,220,${a * 1.6})`); cg.addColorStop(1, `rgba(40,25,110,${a * 2.2})`);
        g.fillStyle = cg; g.fill();
        g.strokeStyle = 'rgba(0,0,0,.35)'; g.lineWidth = 1; g.stroke();
        g.beginPath(); g.moveTo(x0 + 8, y0 + 1.2); g.lineTo(x0 + w - 8, y0 + 1.2); g.strokeStyle = 'rgba(255,255,255,.07)'; g.stroke();
      }
      return c;
    }
    // Декоративный кристалл-призма для фона
    function prism(g, cx, by, w, h, ang, p) {
      g.save(); g.translate(cx, by); g.rotate(ang);
      g.shadowColor = p.glow; g.shadowBlur = w * 0.9;
      const top = -h, sh = -h * 0.72;
      g.beginPath(); g.moveTo(-w / 2, 0); g.lineTo(-w / 2, sh); g.lineTo(0, top); g.lineTo(w / 2, sh); g.lineTo(w / 2, 0); g.closePath(); g.fillStyle = p.dark; g.fill();
      g.shadowBlur = 0;
      g.beginPath(); g.moveTo(-w / 2, 0); g.lineTo(-w / 2, sh); g.lineTo(0, top); g.lineTo(-w * 0.08, sh); g.lineTo(-w * 0.08, 0); g.closePath(); g.fillStyle = mix(p.base, p.dark, 0.25); g.fill();
      g.beginPath(); g.moveTo(-w * 0.08, 0); g.lineTo(-w * 0.08, sh); g.lineTo(0, top); g.lineTo(w * 0.22, sh); g.lineTo(w * 0.22, 0); g.closePath();
      const lg = g.createLinearGradient(0, top, 0, 0); lg.addColorStop(0, p.light); lg.addColorStop(1, p.base); g.fillStyle = lg; g.fill();
      g.beginPath(); g.moveTo(w * 0.22, 0); g.lineTo(w * 0.22, sh); g.lineTo(0, top); g.lineTo(w / 2, sh); g.lineTo(w / 2, 0); g.closePath(); g.fillStyle = mix(p.base, p.light, 0.25); g.fill();
      g.strokeStyle = 'rgba(255,255,255,.35)'; g.lineWidth = 1; g.beginPath(); g.moveTo(-w * 0.08, -2); g.lineTo(-w * 0.08, sh); g.lineTo(0, top); g.stroke();
      g.restore();
    }
    const CRYS = [
      { base: '#9c4dff', light: '#f0d0ff', dark: '#3a0c80', glow: '#b86bff' },
      { base: '#4c7dff', light: '#cfe0ff', dark: '#14247a', glow: '#5a8cff' },
      { base: '#d04dff', light: '#ffd0fb', dark: '#5a0a7a', glow: '#e070ff' },
    ];
    function background(menu) {
      const c = mk(W0 * k, H0 * k), g = c.getContext('2d');
      g.scale(k, k);
      const bg = g.createRadialGradient(W0 / 2, menu ? 120 : 200, 20, W0 / 2, 260, 420);
      bg.addColorStop(0, menu ? '#3a1a7e' : '#2a1666'); bg.addColorStop(0.45, '#1a0d4a'); bg.addColorStop(1, '#07041a');
      g.fillStyle = bg; g.fillRect(0, 0, W0, H0);
      for (const [nx, ny, nr, col] of [[70, 120, 160, 'rgba(70,90,255,.18)'], [380, 380, 190, 'rgba(190,60,255,.15)'], [330, 70, 130, 'rgba(255,90,200,.08)']]) {
        const ng = g.createRadialGradient(nx, ny, 0, nx, ny, nr); ng.addColorStop(0, col); ng.addColorStop(1, 'rgba(0,0,0,0)'); g.fillStyle = ng; g.fillRect(0, 0, W0, H0);
      }
      const r0 = ENG.makeRng(menu ? 7 : 11);
      for (let q = 0; q < 70; q++) { g.fillStyle = `rgba(255,255,255,${0.15 + r0.next() * 0.45})`; g.beginPath(); g.arc(r0.next() * W0, r0.next() * H0, 0.4 + r0.next() * 1.1, 0, Math.PI * 2); g.fill(); }
      if (menu) {
        // Кристаллы по краям: внизу слева и справа
        const L = [[18, H0 + 6, 26, 120, -0.32, 0], [46, H0 + 4, 34, 160, -0.12, 2], [80, H0 + 8, 22, 92, 0.15, 1], [6, H0 - 40, 16, 64, -0.6, 1], [112, H0 + 10, 16, 56, 0.4, 0]];
        for (const [cx, by, w, h, a, p] of L) { prism(g, cx, by, w, h, a, CRYS[p]); prism(g, W0 - cx, by, w, h, -a, CRYS[(p + 1) % 3]); }
        const vg = g.createLinearGradient(0, H0 - 60, 0, H0); vg.addColorStop(0, 'rgba(10,5,30,0)'); vg.addColorStop(1, 'rgba(10,5,30,.55)'); g.fillStyle = vg; g.fillRect(0, H0 - 60, W0, 60);
      } else {
        const L = [[10, H0 + 14, 16, 62, -0.35, 0], [30, H0 + 16, 20, 80, -0.1, 1]];
        for (const [cx, by, w, h, a, p] of L) { g.globalAlpha = 0.6; prism(g, cx, by, w, h, a, CRYS[p]); prism(g, W0 - cx, by, w, h, -a, CRYS[(p + 1) % 3]); g.globalAlpha = 1; }
      }
      const vig = g.createRadialGradient(W0 / 2, H0 / 2, H0 * 0.35, W0 / 2, H0 / 2, H0 * 0.78);
      vig.addColorStop(0, 'rgba(0,0,0,0)'); vig.addColorStop(1, 'rgba(0,0,0,.45)'); g.fillStyle = vig; g.fillRect(0, 0, W0, H0);
      return c;
    }
    function buildSprites() {
      freeSprites();
      const S = Math.ceil(CS * SPR * k), C = Math.ceil(CS * k);
      sprites = {
        gem: PAL.map((p, i) => gemSprite(i, p, S)),
        shine: PAL.map((p, i) => shineFrames(i, S)),
        rocket: PAL.map((p) => rocketSprite(p, S)),
        blades: PAL.map((p) => propBlades(p, S)),
        hub: hubSprite(S),
        bomb: PAL.map((p) => bombSprite(p, S)),
        facets: megaFacets(S),
        glow: [...PAL.map((p) => mix(p.glow, p.glow, 0)), ...EXTRA.map((e) => mix(e, e, 0))].map(glowSprite),
        ice: [null, iceSprite(1, C), iceSprite(2, C)],
        lock: lockSprite(C),
        stone: [null, stoneSprite(1, C), stoneSprite(2, C)],
        panel: boardPanel(),
        bgMenu: background(true),
        bgGame: background(false),
      };
      icons = null;
      spritesDirty = false;
    }
    function freeSprites() {
      if (!sprites) return;
      const kill = (c) => { if (c && c.getContext) { c.width = 0; c.height = 0; } else if (Array.isArray(c)) c.forEach(kill); };
      Object.values(sprites).forEach(kill);
      sprites = null;
    }
    // Иконки для DOM (цели уровня) — из тех же спрайтов
    function getIcons() {
      if (!sprites) buildSprites();
      if (icons) return icons;
      const at = (src, sz = 64, crop = 1) => { const c = mk(sz, sz), g = c.getContext('2d'); const s = src.width * crop; g.drawImage(src, (src.width - s) / 2, (src.height - s) / 2, s, s, 0, 0, sz, sz); return c.toDataURL(); };
      icons = { gem: sprites.gem.map((s) => at(s, 64, 0.72)), ice: at(sprites.ice[2]), lock: at(sprites.lock, 64, 0.55), stone: at(sprites.stone[2], 64, 0.9), score: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path d="M50 5l12.6 30 32.4 2.7-24.6 21.2 7.5 31.6L50 73.6 22.1 90.5l7.5-31.6L5 37.7l32.4-2.7z" fill="#ffd23a" stroke="#a65a00" stroke-width="6" stroke-linejoin="round"/></svg>') };
      return icons;
    }

    // ----- Видимое состояние поля -----
    let g = null; // текущая партия движка
    const views = new Map();
    let cellObs = [];
    const disp = { score: 0, moves: 0, collected: [0, 0, 0, 0, 0, 0] };
    let shownScore = 0;
    const fxList = [], parts = [], pool = [], popups = [];
    let vis = 0; // визуальное время (мс), замедляется в «слоу-мо», стоит на паузе
    let timeScale = 1, slowUntil = 0, shakeAmp = 0, shakeUntil = 0, shakeDur = 1, flashA = 0, dimA = 0, dimUntil = 0, dimCell = -1;
    let autoDrop = 0, frameAvg = 16.7, slowFrames = 0, testSpeed = 1;
    const timers = [];
    const after = (ms, fn) => timers.push({ at: vis + ms, fn });
    const reduce = () => settings.reduce;
    const qf = () => ({ high: 1, mid: 0.65, low: 0.42, pixel: 0.3 }[settings.quality] || 0.42) * (autoDrop ? 0.6 / autoDrop : 1) * (reduce() ? 0.6 : 1);
    const pmax = () => Math.round(900 * qf());

    function makeView(p, i, fromRow) {
      const [r, c] = rc(i);
      const v = { id: p.id, kind: p.kind, color: p.color, up: p.up || 0, x: c, y: fromRow ?? r, tw: null, ph: Math.random(), born: vis, die: 0, toward: -1, flash: 0, land: 0, shards: false };
      views.set(p.id, v);
      return v;
    }
    function tween(v, x1, y1, dur, kind, delay = 0) { v.tw = { x0: v.x, y0: v.y, x1, y1, t0: vis + delay, dur, kind }; }
    function syncFromEngine(intro) {
      views.clear();
      cellObs = g.cells.map((c) => ({ ice: c.ice, lock: c.lock, stone: c.stone, shake: 0 }));
      let maxEnd = 0;
      g.cells.forEach((c, i) => {
        if (!c.piece) return;
        const [r, cc] = rc(i);
        const v = makeView(c.piece, i, intro ? r - NN - 1 - ((NN - 1 - r) * 0.12) : r);
        if (intro) { const delay = (NN - 1 - r) * 55 + cc * 22 + 120, dur = 160 * Math.sqrt(NN + 1) + 90; tween(v, cc, r, dur, 'fall', delay); maxEnd = Math.max(maxEnd, delay + dur); }
      });
      disp.score = g.score; disp.moves = g.moves; disp.collected = g.collected.slice();
      return maxEnd;
    }
    // Страховка: после всех анимаций картинка совпадает с движком
    function reconcile() {
      const seen = new Set();
      g.cells.forEach((c, i) => {
        cellObs[i].ice = c.ice; cellObs[i].lock = c.lock; cellObs[i].stone = c.stone;
        if (!c.piece) return;
        seen.add(c.piece.id);
        let v = views.get(c.piece.id);
        if (!v || v.die) v = makeView(c.piece, i);
        const [r, cc] = rc(i);
        v.kind = c.piece.kind; v.color = c.piece.color; v.up = c.piece.up || 0;
        if (!v.tw) { v.x = cc; v.y = r; }
      });
      for (const [id, v] of views) if (!seen.has(id) && !v.die) views.delete(id);
      disp.score = g.score; disp.moves = g.moves; disp.collected = g.collected.slice();
    }

    // ----- Частицы (пул без выделения памяти в кадре) -----
    function emit(type, px, py, vx, vy, life, size, col, grav = 0, essential = false) {
      if (parts.length >= (essential ? 1400 : pmax())) return;
      const p = pool.pop() || {};
      p.type = type; p.x = px; p.y = py; p.vx = vx; p.vy = vy; p.life = life; p.max = life; p.size = size; p.col = col; p.g = grav; p.rot = Math.random() * 6.28; p.vr = rnd(-10, 10);
      parts.push(p);
    }
    function shatter(px, py, col, n = 1) {
      const q = qf(), cnt = Math.max(3, Math.round(10 * q * n));
      for (let s = 0; s < cnt; s++) { const a = Math.random() * 6.28, sp = rnd(80, 230); emit(0, px, py, Math.cos(a) * sp, Math.sin(a) * sp - 60, rnd(0.4, 0.7), rnd(3.5, 7), col, 520); }
      for (let s = 0; s < Math.round(6 * q * n); s++) { const a = Math.random() * 6.28, sp = rnd(30, 140); emit(1, px, py, Math.cos(a) * sp, Math.sin(a) * sp, rnd(0.25, 0.5), rnd(12, 22), col, 60); }
      emit(1, px, py, 0, 0, 0.2, 54, 6, 0, true);
    }
    function sparks(px, py, col, n, speed = 160, life = 0.55, size = 16) {
      const cnt = Math.round(n * qf());
      for (let s = 0; s < cnt; s++) { const a = Math.random() * 6.28, sp = rnd(speed * 0.3, speed); emit(1, px, py, Math.cos(a) * sp, Math.sin(a) * sp, rnd(life * 0.6, life), rnd(size * 0.6, size), col, 90); }
    }
    function updateParts(dt) {
      for (let q = parts.length - 1; q >= 0; q--) {
        const p = parts[q];
        p.life -= dt;
        if (p.life <= 0) { pool.push(p); parts[q] = parts[parts.length - 1]; parts.pop(); continue; }
        const drag = 1 - Math.min(0.9, 1.8 * dt);
        p.vx *= drag; p.vy = p.vy * drag + p.g * dt; p.x += p.vx * dt; p.y += p.vy * dt; p.rot += p.vr * dt;
      }
    }
    function drawParts() {
      for (const p of parts) {
        if (p.type !== 0 && p.type !== 3) continue;
        const a = p.life / p.max;
        x.globalAlpha = a;
        if (p.type === 0) {
          x.save(); x.translate(p.x, p.y); x.rotate(p.rot); x.fillStyle = a > 0.5 ? lightOf(p.col) : colorOf(p.col);
          x.beginPath(); x.moveTo(0, -p.size); x.lineTo(p.size * 0.7, p.size * 0.6); x.lineTo(-p.size * 0.6, p.size * 0.4); x.closePath(); x.fill(); x.restore();
        } else { // мягкая пыль
          const d = p.size * (3.2 - a * 1.4); x.globalAlpha = a * 0.45; x.drawImage(sprites.glow[p.col], p.x - d / 2, p.y - d / 2, d, d);
        }
      }
      x.globalCompositeOperation = 'lighter';
      for (const p of parts) {
        if (p.type !== 1 && p.type !== 2) continue;
        const a = p.life / p.max, s = p.size * (p.type === 2 ? 1 : 0.5 + a * 0.5);
        x.globalAlpha = Math.min(1, a * 1.4);
        if (p.type === 1) x.drawImage(sprites.glow[p.col], p.x - s / 2, p.y - s / 2, s, s);
        else star4(x, p.x, p.y, s * (0.4 + 0.6 * Math.sin(a * Math.PI)), 1);
      }
      x.globalCompositeOperation = 'source-over'; x.globalAlpha = 1;
    }
    // Близкие по месту и времени очки сливаются в одно растущее число — без столбиков из цифр
    function popup(px, py, pts) {
      for (const p of popups) if (vis - p.t0 < 450 && Math.hypot(p.x - px, p.y - py) < 75) { p.pts += pts; p.t0 = vis - 120; p.size = p.pts >= 1000 ? 21 : 17; return; }
      popups.push({ x: px, y: py, pts, t0: vis, size: pts >= 1000 ? 21 : 17 });
    }
    function shake(amp, ms) { if (reduce()) return; if (amp >= shakeAmp * (shakeUntil - vis) / shakeDur || vis > shakeUntil) { shakeAmp = amp; shakeUntil = vis + ms; shakeDur = ms; } }
    function flash(a) { if (reduce()) return; flashA = Math.max(flashA, a); }
    function slowmo(ms) { if (reduce()) return; slowUntil = performance.now() + ms; }
    function dim(i, ms, a = 0.55) { dimCell = i; dimUntil = vis + ms; dimA = Math.max(dimA, a); }

    // ----- Отрисовка фигур -----
    function drawPiece(v, cx, cy, sc, now) {
      if (v.up && v.kind !== 'n') { // супер-бонус: золотое сияние, вращающееся кольцо и чуть крупнее
        const pulse = 0.75 + 0.25 * Math.sin(now * 0.006 + v.ph * 6), gs = CS * 1.55 * sc * pulse;
        x.save(); x.globalCompositeOperation = 'lighter'; x.globalAlpha *= 0.75; x.drawImage(sprites.glow[7], cx - gs / 2, cy - gs / 2, gs, gs);
        x.translate(cx, cy); x.rotate(now * 0.002); x.strokeStyle = '#ffe27a'; x.lineWidth = 2; x.setLineDash([5, 4]); x.beginPath(); x.arc(0, 0, CS * 0.47 * sc, 0, 6.28); x.stroke();
        x.setLineDash([]); star4(x, CS * 0.47 * sc, 0, 5 * sc, 1); x.restore();
        sc *= 1.07;
      }
      const S = CS * SPR * sc;
      if (v.kind === 'n') {
        x.drawImage(sprites.gem[v.color], cx - S / 2, cy - S / 2, S, S);
        if (settings.quality !== 'pixel') {
          const ph = (now / 6800 + v.ph) % 1;
          if (ph < 0.085) { x.globalAlpha = 0.85; x.drawImage(sprites.shine[v.color][Math.floor(ph / 0.085 * 10)], cx - S / 2, cy - S / 2, S, S); x.globalAlpha = 1; }
        }
      } else if (v.kind === 'rh' || v.kind === 'rv') {
        x.save(); x.translate(cx, cy); if (v.kind === 'rv') x.rotate(-Math.PI / 2);
        const fl = 0.75 + 0.25 * Math.sin(now * 0.04 + v.ph * 9);
        x.globalCompositeOperation = 'lighter'; x.globalAlpha = 0.9;
        const gs = CS * 0.55 * fl * sc; x.drawImage(sprites.glow[7], -CS * 0.44 * sc - gs / 2, -gs / 2, gs, gs);
        x.globalCompositeOperation = 'source-over'; x.globalAlpha = 1;
        const bob = Math.sin(now * 0.005 + v.ph * 6) * 1.2;
        x.drawImage(sprites.rocket[v.color], -S / 2 + bob, -S / 2, S, S); x.restore();
      } else if (v.kind === 'p') {
        x.save(); x.translate(cx, cy - Math.sin(now * 0.004 + v.ph * 6) * 1.5);
        x.save(); x.rotate(now * 0.006 + v.ph * 6); x.drawImage(sprites.blades[v.color], -S / 2, -S / 2, S, S); x.restore();
        x.drawImage(sprites.hub, -S / 2, -S / 2, S, S); x.restore();
      } else if (v.kind === 'b') {
        const pulse = 1 + Math.sin(now * 0.006 + v.ph * 6) * 0.025;
        x.drawImage(sprites.bomb[v.color], cx - S / 2 * pulse, cy - S / 2 * pulse, S * pulse, S * pulse);
        const fx0 = cx + CS * 0.27 * sc, fy0 = cy - CS * 0.42 * sc, fl = 0.6 + 0.4 * Math.abs(Math.sin(now * 0.03 + v.ph * 7));
        x.globalCompositeOperation = 'lighter'; const gs = CS * 0.42 * fl * sc; x.drawImage(sprites.glow[7], fx0 - gs / 2, fy0 - gs / 2, gs, gs); x.globalCompositeOperation = 'source-over';
        if (hot && Math.random() < 0.08 * qf()) emit(1, fx0, fy0, rnd(-40, 40), rnd(-70, -20), 0.35, 8, 7, 120);
      } else if (v.kind === 'm') drawMega(cx, cy, CS * 0.4 * sc, now, v.ph);
    }
    function drawMega(cx, cy, rad, now, ph = 0) {
      x.save(); x.translate(cx, cy);
      x.globalCompositeOperation = 'lighter'; x.globalAlpha = 0.55 + 0.2 * Math.sin(now * 0.004);
      x.drawImage(sprites.glow[6], -rad * 2, -rad * 2, rad * 4, rad * 4);
      x.globalCompositeOperation = 'source-over'; x.globalAlpha = 1;
      x.beginPath(); x.arc(0, 0, rad, 0, Math.PI * 2); x.save(); x.clip();
      let fill;
      if (x.createConicGradient) { fill = x.createConicGradient(now * 0.0013 + ph * 6, 0, 0); ['#ff3d5e', '#ffd84a', '#3cf079', '#56f3ff', '#4a90ff', '#c06bff', '#ff3d5e'].forEach((c, q) => fill.addColorStop(q / 6, c)); }
      else { fill = x.createLinearGradient(-rad, -rad, rad, rad); ['#ff3d5e', '#ffd84a', '#3cf079', '#4a90ff', '#c06bff'].forEach((c, q) => fill.addColorStop(q / 4, c)); }
      x.fillStyle = fill; x.fillRect(-rad, -rad, rad * 2, rad * 2);
      const sg = x.createRadialGradient(-rad * 0.3, -rad * 0.35, rad * 0.1, 0, 0, rad);
      sg.addColorStop(0, 'rgba(255,255,255,.5)'); sg.addColorStop(0.45, 'rgba(255,255,255,0)'); sg.addColorStop(1, 'rgba(30,0,70,.6)');
      x.fillStyle = sg; x.fillRect(-rad, -rad, rad * 2, rad * 2);
      x.rotate(-now * 0.0006); const D = rad * 1.04 * SPR / 0.42; x.drawImage(sprites.facets, -D / 2, -D / 2, D, D);
      x.restore();
      x.beginPath(); x.arc(0, 0, rad, 0, Math.PI * 2); x.strokeStyle = 'rgba(255,255,255,.7)'; x.lineWidth = 1.6; x.stroke();
      star4(x, -rad * 0.35, -rad * 0.4, rad * 0.35, 0.9);
      if (settings.quality === 'high' && ((now * 0.001 + ph * 5) % 1.3) < 0.12) { // тонкие электрические разряды
        x.strokeStyle = 'rgba(200,250,255,.9)'; x.lineWidth = 1.2; x.beginPath();
        const a = (Math.floor(now / 90) * 2.39 + ph * 9) % 6.28;
        let px0 = Math.cos(a) * rad, py0 = Math.sin(a) * rad; x.moveTo(px0, py0);
        for (let q = 1; q <= 4; q++) { px0 = Math.cos(a) * rad * (1 + q * 0.12) + rnd(-3, 3); py0 = Math.sin(a) * rad * (1 + q * 0.12) + rnd(-3, 3); x.lineTo(px0, py0); }
        x.stroke();
      }
      x.restore();
    }
    function viewPos(v, now) {
      if (v.tw) {
        const t = v.tw, p = clamp((now - t.t0) / t.dur, 0, 1);
        let e;
        if (t.kind === 'fall') {
          e = p < 0.78 ? Math.pow(p / 0.78, 2) : 1 - Math.sin((p - 0.78) / 0.22 * Math.PI) * 0.09 / Math.max(1, Math.abs(t.y1 - t.y0));
          if (p >= 0.78 && !v.land) v.land = now;
        } else if (t.kind === 'shuffle') e = easeInOut(p);
        else if (t.kind === 'back') e = Math.sin(p * Math.PI) * 0.5;
        else e = easeInOut(p);
        v.x = t.x0 + (t.x1 - t.x0) * e; v.y = t.y0 + (t.y1 - t.y0) * e;
        if (t.kind === 'shuffle') { const s = Math.sin(p * Math.PI) * 1.4, ang = p * Math.PI * 2 + v.ph * 6; v.x += Math.cos(ang) * s; v.y += Math.sin(ang) * s * 0.8; }
        if (p >= 1) { v.x = t.kind === 'back' ? t.x0 : t.x1; v.y = t.kind === 'back' ? t.y0 : t.y1; v.tw = null; }
      }
    }

    // ----- Эффекты бонусов -----
    function fxRocket(i, h, vv, color, diag) {
      const [r, c] = rc(i), t0 = vis, dirs = [];
      if (h) dirs.push([1, 0], [-1, 0]);
      if (vv) dirs.push([0, 1], [0, -1]);
      if (diag) dirs.push([1, 1], [1, -1], [-1, 1], [-1, -1]);
      const ox = cellX(c), oy = cellY(r);
      const cellT = diag ? T.rocketCell * 1.3 : T.rocketCell;
      fxList.push({ end: t0 + (NN + 2) * cellT + 260, layer: 1, draw(now, dt) {
        const d = (now - t0) / cellT;
        x.save(); rr(x, BX - 4, BY - 4, BS + 8, BS + 8, 16); x.clip();
        for (const [dx, dy] of dirs) {
          const hx = ox + dx * d * CS, hy = oy + dy * d * CS, tail = Math.max(0, d - 3.2);
          const tx = ox + dx * tail * CS, ty = oy + dy * tail * CS, fade = clamp(1 - (d - NN) / 2, 0, 1);
          const gr = x.createLinearGradient(tx, ty, hx, hy);
          gr.addColorStop(0, 'rgba(255,120,40,0)'); gr.addColorStop(0.7, `rgba(255,170,60,${0.55 * fade})`); gr.addColorStop(1, `rgba(255,255,220,${0.95 * fade})`);
          x.globalCompositeOperation = 'lighter'; x.strokeStyle = gr; x.lineCap = 'round';
          x.lineWidth = CS * 0.34; x.globalAlpha = 0.45; x.beginPath(); x.moveTo(tx, ty); x.lineTo(hx, hy); x.stroke();
          x.lineWidth = CS * 0.12; x.globalAlpha = 1; x.stroke();
          x.globalCompositeOperation = 'source-over';
          if (d < NN + 1) {
            x.save(); x.translate(hx, hy); x.rotate(Math.atan2(dy, dx)); const S = CS * SPR * 0.85; x.drawImage(sprites.rocket[color], -S / 2, -S / 2, S, S); x.restore();
            if (Math.random() < 0.9 * qf()) emit(1, hx - dx * CS * 0.35, hy - dy * CS * 0.35, -dx * rnd(20, 90) + rnd(-40, 40), -dy * rnd(20, 90) + rnd(-40, 40), 0.45, rnd(10, 18), Math.random() < 0.5 ? 7 : color, 0);
          }
        }
        x.restore();
      } });
      const fl = 0.18; flash(fl * 0.5); shake(2.5, 160); sfx('rocket');
      sparks(ox, oy, 7, 14, 200);
    }
    function fxBomb(i, rad, color) {
      const [r, c] = rc(i), cx = cellX(c), cy = cellY(r), t0 = vis + T.bombDelay * 0.6, R = (rad + 0.6) * CS;
      fxList.push({ end: t0 + 620, layer: 1, draw(now) {
        const p = clamp((now - t0) / 560, 0, 1);
        if (now < t0) return;
        x.globalCompositeOperation = 'lighter';
        const rr0 = easeOut(p) * R;
        x.globalAlpha = (1 - p) * 0.9; x.strokeStyle = '#ffe9b0'; x.lineWidth = CS * 0.35 * (1 - p) + 2; x.beginPath(); x.arc(cx, cy, rr0, 0, 6.28); x.stroke();
        x.globalAlpha = (1 - p) * 0.6; x.strokeStyle = colorOf(color); x.lineWidth = CS * 0.2 * (1 - p) + 1; x.beginPath(); x.arc(cx, cy, rr0 * 0.72, 0, 6.28); x.stroke();
        if (p < 0.35) { const fs = R * 2.2 * (0.5 + p); x.globalAlpha = (0.35 - p) / 0.35; x.drawImage(sprites.glow[7], cx - fs / 2, cy - fs / 2, fs, fs); }
        x.globalAlpha = 1; x.globalCompositeOperation = 'source-over';
      } });
      after(T.bombDelay * 0.6, () => {
        flash(rad > 2 ? 0.45 : 0.3); shake(rad > 2 ? 6 : 4, 320); sfx('bomb', rad > 2);
        sparks(cx, cy, 7, 40 * (rad / 2), 340, 0.8, 22); sparks(cx, cy, color, 24, 260, 0.7, 18);
        for (let q = 0; q < 12 * qf(); q++) { const a = Math.random() * 6.28, sp = rnd(30, 110); emit(3, cx, cy, Math.cos(a) * sp, Math.sin(a) * sp, rnd(0.6, 1), rnd(10, 18), 9, -20); }
      });
    }
    function fxPropUp(i, color) {
      const [r, c] = rc(i), cx = cellX(c), cy = cellY(r), t0 = vis;
      fxList.push({ end: t0 + T.propLift + 20, layer: 2, draw(now) {
        const p = clamp((now - t0) / T.propLift, 0, 1), S = CS * SPR * (1 + p * 0.25);
        x.save(); x.translate(cx, cy - p * CS * 0.2); x.save(); x.rotate(now * 0.03); x.drawImage(sprites.blades[color], -S / 2, -S / 2, S, S); x.restore(); x.drawImage(sprites.hub, -S / 2, -S / 2, S, S); x.restore();
      } });
      sparks(cx, cy, color, 10, 150);
    }
    function fxPropFly(from, to, dur, carry, color) {
      const [r0, c0] = rc(from), [r1, c1] = rc(to);
      const ax = cellX(c0), ay = cellY(r0) - CS * 0.2, bx = cellX(c1), by = cellY(r1), t0 = vis;
      const mx = (ax + bx) / 2, my = (ay + by) / 2, dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1;
      const side = (from + to) % 2 ? 1 : -1, bend = Math.min(2.2 * CS, len * 0.6) + CS * 0.6;
      const qx = mx - dy / len * bend * side, qy = my + dx / len * bend * side - CS * 0.8;
      sfx('prop');
      fxList.push({ end: t0 + dur + 30, layer: 2, draw(now) {
        const p = easeInOut(clamp((now - t0) / dur, 0, 1)), ip = 1 - p;
        const px = ip * ip * ax + 2 * ip * p * qx + p * p * bx, py = ip * ip * ay + 2 * ip * p * qy + p * p * by;
        const tx = 2 * ip * (qx - ax) + 2 * p * (bx - qx);
        const S = CS * SPR * (1.2 - 0.2 * Math.abs(p - 0.5) * 2);
        if (Math.random() < 0.95 * qf()) { emit(1, px + rnd(-6, 6), py + rnd(-6, 6), rnd(-30, 30), rnd(-30, 30), 0.5, rnd(10, 18), Math.random() < 0.4 ? 6 : color, 40); if (Math.random() < 0.3) emit(2, px, py, rnd(-40, 40), rnd(-40, 40), 0.5, 7, 6, 0); }
        x.save(); x.translate(px, py); x.rotate(clamp(tx / 900, -0.5, 0.5));
        if (carry) { const cs = CS * SPR * 0.62; x.save(); x.translate(0, CS * 0.32); if (carry === 'rv') x.rotate(-Math.PI / 2); x.drawImage(carry === 'b' ? sprites.bomb[color] : sprites.rocket[color], -cs / 2, -cs / 2, cs, cs); x.restore(); }
        x.save(); x.rotate(now * 0.035); x.drawImage(sprites.blades[color], -S / 2, -S / 2, S, S); x.restore(); x.drawImage(sprites.hub, -S / 2, -S / 2, S, S);
        x.restore();
      } });
    }
    function fxBeam(i, targets, color, transform) {
      const [r, c] = rc(i), cx = cellX(c), cy = cellY(r), t0 = vis, end = t0 + (transform ? T.megaBeam + targets.length * T.megaChain : T.megaBeam) + 120;
      const step = Math.min(26, 300 / Math.max(1, targets.length));
      sfx('mega');
      if (targets.length > 6 || transform) { dim(i, end - t0, 0.5); }
      let segSeed = 0, lastSeg = 0;
      fxList.push({ end, layer: 2, draw(now) {
        const p = clamp((now - t0) / 260, 0, 1), rise = easeOut(p) * CS * 0.3;
        if (now - lastSeg > 55) { segSeed++; lastSeg = now; }
        const r0 = ENG.makeRng(segSeed * 977 + i);
        x.globalCompositeOperation = 'lighter';
        targets.forEach((j, q) => {
          const ts = t0 + 80 + q * step;
          if (now < ts || now > t0 + T.megaBeam + 60 + (transform ? q * T.megaChain : 0)) return;
          const [rj, cj] = rc(j), tx = cellX(cj), ty = cellY(rj);
          x.strokeStyle = colorOf(color); x.lineWidth = 5; x.globalAlpha = 0.35; x.beginPath(); x.moveTo(cx, cy - rise);
          const segs = 6; for (let s = 1; s < segs; s++) { const f = s / segs; x.lineTo(cx + (tx - cx) * f + (r0.next() - 0.5) * 12, cy - rise + (ty - cy + rise) * f + (r0.next() - 0.5) * 12); } x.lineTo(tx, ty); x.stroke();
          x.strokeStyle = '#ffffff'; x.lineWidth = 1.6; x.globalAlpha = 0.95; x.stroke();
          const gs = CS * 0.9; x.globalAlpha = 0.6; x.drawImage(sprites.glow[color], tx - gs / 2, ty - gs / 2, gs, gs);
        });
        x.globalCompositeOperation = 'source-over'; x.globalAlpha = 1;
        if (now < t0 + T.megaBeam + 40) drawMega(cx, cy - rise, CS * 0.4 * (1 + p * 0.3), now * 3);
      } });
      targets.forEach((j, q) => after(80 + q * step, () => sfx('beam')));
    }
    function fxMegaMega(i) {
      const [r, c] = rc(i), cx = cellX(c), cy = cellY(r), t0 = vis, dur = T.mmDelay + NN * 2 * T.mmStep + 300;
      dim(i, dur, 0.65); slowmo(450); sfx('megamega');
      fxList.push({ end: t0 + dur, layer: 2, draw(now) {
        if (now < t0 + T.mmDelay) drawMega(cx, cy - CS * 0.3, CS * 0.45 * (1 + (now - t0) / T.mmDelay * 0.6), now * 4);
        const q = clamp((now - t0 - T.mmDelay) / (dur - T.mmDelay), 0, 1);
        if (q > 0) {
          x.globalCompositeOperation = 'lighter';
          const R = q * CS * NN * 1.5;
          x.globalAlpha = Math.min(1, q * 6) * (1 - q); x.lineWidth = CS * 0.4 * (1 - q) + 2;
          ['#ff3d5e', '#ffd84a', '#3cf079', '#4a90ff', '#c06bff'].forEach((col, s) => { const rr0 = R - s * 10; if (rr0 < CS * 0.5) return; x.strokeStyle = col; x.beginPath(); x.arc(cx, cy, rr0, 0, 6.28); x.stroke(); });
          x.globalAlpha = 1; x.globalCompositeOperation = 'source-over';
        }
      } });
      after(T.mmDelay, () => { flash(0.6); shake(6, 450); });
    }

    // ----- Проигрыватель шагов движка -----
    const queue = [];
    let cur = null;
    const busy = () => cur !== null || queue.length > 0;
    function enqueue(...steps) { queue.push(...steps); }
    function beginStep(st) {
      cur = { st, t0: vis, ev: 0, dur: 0 };
      if (st.type === 'swap') {
        const va = views.get(st.ida), vb = views.get(st.idb), [ra, ca] = rc(st.a), [rb, cb] = rc(st.b);
        if (st.bounce) { if (va) tween(va, cb, rb, 300, 'back'); if (vb) tween(vb, ca, ra, 300, 'back'); cur.dur = 300; sfx('bad'); }
        else { if (va) tween(va, cb, rb, 170, 'swap'); if (vb && !st.merge) tween(vb, ca, ra, 170, 'swap'); cur.dur = 175; sfx('swap'); }
      } else if (st.type === 'wave') {
        cur.dur = st.duration + 300;
        if (st.index > 0 && st.points > 0) sfx('match', 3, st.index);
      } else if (st.type === 'fall') {
        let mx = 0;
        for (const m of st.moves) { const v = views.get(m.id); if (!v) continue; const [r1, c1] = rc(m.to), d = r1 - v.y, dur = 150 * Math.sqrt(Math.max(0.3, d)) + 90; v.land = 0; tween(v, c1, r1, dur, 'fall'); mx = Math.max(mx, dur); }
        for (const s of st.spawns) { const [r1, c1] = rc(s.to), v = makeView(s, s.to, s.fromRow), d = r1 - s.fromRow, dur = 150 * Math.sqrt(d) + 90; tween(v, c1, r1, dur, 'fall'); mx = Math.max(mx, dur); }
        cur.dur = mx + 20;
      } else if (st.type === 'shuffle') {
        for (const m of st.moves) { const v = views.get(m.id); if (!v) continue; const [r1, c1] = rc(m.to); tween(v, c1, r1, 760, 'shuffle'); }
        if (st.recolor) after(380, () => { for (const p of st.recolor) { const v = views.get(p.id); if (v) { v.color = p.color; v.kind = p.kind; v.up = p.up || 0; } } });
        cur.dur = 800; sfx('shuffle'); praiseText('Перемешиваем!', 'info', W0 / 2, BY + BS / 2);
      } else if (st.type === 'wait') cur.dur = st.dur;
      else if (st.type === 'call') { cur.dur = 0; st.fn(); }
    }
    function updatePlayer() {
      for (let guard = 0; guard < 50; guard++) {
        if (!cur) { if (!queue.length) return; beginStep(queue.shift()); }
        const el = vis - cur.t0;
        if (cur.st.type === 'wave') { const evs = cur.st.events; while (cur.ev < evs.length && evs[cur.ev].t <= el) handleEvent(evs[cur.ev++], cur.st); }
        if (el >= cur.dur) {
          if (cur.st.type === 'wave') { const evs = cur.st.events; while (cur.ev < evs.length) handleEvent(evs[cur.ev++], cur.st); }
          cur = null;
          if (!queue.length) { reconcile(); return; }
          continue;
        }
        return;
      }
    }
    function handleEvent(e, st) {
      switch (e.type) {
        case 'match': {
          if (st.index === 0) sfx('match', e.size, 0);
          if (e.special) after(T.create, () => sfx('create'));
          if (e.upgrade) { const [ur, uc] = rc(e.at); after(T.create, () => { praiseText('Супер-бонус!', 'info', cellX(uc), Math.max(BY + 24, cellY(ur) - CS * 0.8)); sfx('mega'); }); }
          break;
        }
        case 'destroy': {
          const v = views.get(e.id);
          if (e.color >= 0) disp.collected[e.color]++;
          if (!v) break;
          v.die = vis; v.toward = e.toward;
          if (e.kind === 'p' || e.kind === 'm') { views.delete(e.id); break; } // их показывает эффект взлёта / лучей
          if (e.toward < 0) sfx('shatter');
          break;
        }
        case 'obs': {
          const o = cellObs[e.i], [r, c] = rc(e.i), cx = cellX(c), cy = cellY(r);
          o[e.what] = e.left; o.shake = vis;
          if (e.what === 'ice') { for (let q = 0; q < 9 * qf() + 3; q++) { const a = Math.random() * 6.28, sp = rnd(60, 200); emit(0, cx + rnd(-15, 15), cy + rnd(-15, 15), Math.cos(a) * sp, Math.sin(a) * sp - 40, rnd(0.4, 0.7), rnd(3, 6), 8, 500); } sfx('ice'); }
          if (e.what === 'lock') { for (let q = 0; q < 8 * qf() + 3; q++) { const a = Math.random() * 6.28, sp = rnd(60, 180); emit(0, cx, cy, Math.cos(a) * sp, Math.sin(a) * sp - 60, rnd(0.4, 0.7), rnd(3, 5), 10, 600); } sparks(cx, cy, 7, 8, 140); sfx('lock'); }
          if (e.what === 'stone') { for (let q = 0; q < 10 * qf() + 3; q++) { const a = Math.random() * 6.28, sp = rnd(50, 180); emit(0, cx, cy, Math.cos(a) * sp, Math.sin(a) * sp - 50, rnd(0.5, 0.8), rnd(4, 8), 9, 600); emit(3, cx, cy, Math.cos(a) * 40, Math.sin(a) * 40, 0.8, 14, 9, -10); } shake(2, 120); sfx('stone'); }
          break;
        }
        case 'create': {
          const v = makeView(e.piece, e.i);
          v.born = vis; v.flash = vis;
          const [r, c] = rc(e.i); sparks(cellX(c), cellY(r), e.piece.color >= 0 ? e.piece.color : 6, 18, 180, 0.6, 18);
          fxList.push(ringFx(cellX(c), cellY(r), e.piece.color >= 0 ? colorOf(e.piece.color) : '#ffffff', vis, 420, CS * 0.9));
          break;
        }
        case 'transform': {
          const v = views.get(e.id);
          if (v) { v.kind = e.kind; v.up = e.up || 0; v.flash = vis; }
          const [r, c] = rc(e.i); sparks(cellX(c), cellY(r), 6, 8, 120);
          if (e.tick) { disp.moves = Math.max(0, disp.moves - 1); sfx('tick', cur ? cur.ev : 0); }
          break;
        }
        case 'tick': disp.moves = Math.max(0, disp.moves - e.n); break;
        case 'rocket': fxRocket(e.i, e.h, e.v, e.color < 0 ? 3 : e.color, e.diag); break;
        case 'bomb': fxBomb(e.i, e.rad, e.color < 0 ? 3 : e.color); break;
        case 'propUp': fxPropUp(e.i, e.color); break;
        case 'propFly': fxPropFly(e.from, e.to, e.dur, e.carry, e.color); break;
        case 'beam': fxBeam(e.i, e.targets, e.color, e.transform); break;
        case 'megamega': fxMegaMega(e.i); break;
        case 'combo': {
          const big = ['mm', 'mb', 'mr', 'mp', 'bb'].includes(e.key);
          if (e.key !== 'mn') { dim(e.i, big ? 1100 : 700, big ? 0.6 : 0.45); sfx('combo'); }
          if (big && e.key !== 'mm') slowmo(380);
          const [r, c] = rc(e.i); sparks(cellX(c), cellY(r), 6, 26, 240, 0.7, 24);
          fxList.push(ringFx(cellX(c), cellY(r), '#ffffff', vis, 500, CS * 1.6));
          break;
        }
        case 'score': {
          disp.score += e.pts;
          const px = BX + (e.c + 0.5) * CS, py = BY + (e.r + 0.5) * CS;
          popup(px, py, e.pts);
          break;
        }
      }
    }
    function ringFx(cx, cy, col, t0, dur, R) {
      return { end: t0 + dur, layer: 1, draw(now) {
        const p = clamp((now - t0) / dur, 0, 1);
        x.globalCompositeOperation = 'lighter'; x.globalAlpha = 1 - p; x.strokeStyle = col; x.lineWidth = 6 * (1 - p) + 1;
        x.beginPath(); x.arc(cx, cy, Math.max(1, easeOut(p) * R), 0, 6.28); x.stroke();
        x.globalAlpha = 1; x.globalCompositeOperation = 'source-over';
      } };
    }

    // ----- Кадр -----
    let hover = -1, sel = -1, cursor = 24, kbMode = false, hint = null, lastInput = 0, boardAlpha = 0;
    let menuMix = 1; // 1 — фон меню, 0 — фон игры
    let screen = 'start';
    const heroSeed = Math.random() * 6;
    const floaters = [[56, 70, 1, 0.55], [392, 84, 5, 0.5], [40, 300, 2, 0.42], [402, 296, 3, 0.45], [118, 34, 0, 0.32], [330, 30, 4, 0.34]];
    const twinkles = Array.from({ length: 16 }, () => [rnd(10, W0 - 10), rnd(10, H0 * 0.75), rnd(0, 6), rnd(0.6, 1.4)]);

    function drawHero(now) {
      const cx = W0 / 2, cy = 102, S = 64, th = now * 0.00055 + heroSeed, tilt = 0.42;
      const p = { light: '#f6d6ff', base: '#b24cff', dark: '#36096f' };
      const n = 8, step = Math.PI * 2 / n, V = [];
      for (let q = 0; q < n; q++) V.push([Math.cos((q + 0.5) * step) * 0.52, -0.46, Math.sin((q + 0.5) * step) * 0.52]);
      for (let q = 0; q < n; q++) V.push([Math.cos(q * step), -0.08, Math.sin(q * step)]);
      V.push([0, 1.08, 0]);
      const P = V.map(([vx, vy, vz]) => {
        const x1 = vx * Math.cos(th) - vz * Math.sin(th), z1 = vx * Math.sin(th) + vz * Math.cos(th);
        const y2 = vy * Math.cos(tilt) - z1 * Math.sin(tilt), z2 = vy * Math.sin(tilt) + z1 * Math.cos(tilt);
        return [x1, y2, z2];
      });
      const F = [[...Array(n).keys()]];
      for (let q = 0; q < n; q++) { const t1 = q, t2 = (q + 1) % n, g1 = n + q, g2 = n + (q + 1) % n; F.push([g1, g2, t1]); F.push([t1, g2, t2]); F.push([g1, 2 * n, g2]); }
      const Lt = [-0.35, -0.65, -0.68], ll = Math.hypot(...Lt), L = Lt.map((a) => a / ll);
      const faces = F.map((f) => {
        const pts = f.map((q) => P[q]), z = pts.reduce((a, q) => a + q[2], 0) / pts.length;
        const a = pts[0], b = pts[1], c = pts[2], u1 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], u2 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        let nrm = [u1[1] * u2[2] - u1[2] * u2[1], u1[2] * u2[0] - u1[0] * u2[2], u1[0] * u2[1] - u1[1] * u2[0]];
        const cen = [pts.reduce((s, q) => s + q[0], 0) / pts.length, pts.reduce((s, q) => s + q[1], 0) / pts.length - 0.2, pts.reduce((s, q) => s + q[2], 0) / pts.length];
        if (nrm[0] * cen[0] + nrm[1] * cen[1] + nrm[2] * cen[2] < 0) nrm = nrm.map((v) => -v);
        const nl = Math.hypot(...nrm) || 1;
        return { pts, z, lam: (nrm[0] * L[0] + nrm[1] * L[1] + nrm[2] * L[2]) / nl, front: nrm[2] < 0 };
      }).sort((a, b) => b.z - a.z);
      const A = x.globalAlpha;
      x.save();
      x.globalCompositeOperation = 'lighter';
      const gs = S * 4.2; x.globalAlpha = A * (0.5 + 0.12 * Math.sin(now * 0.002)); x.drawImage(sprites.glow[4], cx - gs / 2, cy - gs / 2 + 10, gs, gs);
      x.globalAlpha = A; x.globalCompositeOperation = 'source-over';
      for (const f of faces) {
        const t = clamp((f.lam + 1) / 2, 0, 1);
        x.beginPath(); f.pts.forEach(([a, b], q) => (q ? x.lineTo(cx + a * S, cy + b * S) : x.moveTo(cx + a * S, cy + b * S))); x.closePath();
        x.globalAlpha = A * (f.front ? 0.96 : 0.75);
        x.fillStyle = t > 0.93 ? mix(p.light, '#ffffff', (t - 0.93) / 0.07) : t > 0.5 ? mix(p.base, p.light, (t - 0.5) / 0.43) : mix(p.dark, p.base, t / 0.5);
        x.fill(); x.strokeStyle = 'rgba(255,230,255,.35)'; x.lineWidth = 1; x.stroke();
      }
      x.globalAlpha = A;
      const sp = (now * 0.0011) % 1;
      if (sp < 0.2) { const v = P[n + Math.floor(now / 1000) % n]; if (v[2] < 0) star4(x, cx + v[0] * S, cy + v[1] * S, 16, Math.sin(sp / 0.2 * Math.PI)); }
      star4(x, cx - S * 0.25, cy - S * 0.38, 10, 0.5 + 0.5 * Math.sin(now * 0.003));
      x.restore();
      if (hot && Math.random() < 0.12 * qf()) { const a = Math.random() * 6.28; emit(2, cx + Math.cos(a) * S * 0.9, cy + Math.sin(a) * S * 0.6, Math.cos(a) * 30, Math.sin(a) * 30 - 10, 0.9, rnd(5, 9), 6, 0); }
    }

    function draw(now, dt) {
      x.setTransform(k, 0, 0, k, 0, 0);
      const target = screen === 'game' ? 0 : 1;
      menuMix += (target - menuMix) * Math.min(1, dt * (reduce() ? 20 : 6));
      x.globalAlpha = 1; x.drawImage(sprites.bgGame, 0, 0, W0, H0);
      if (menuMix > 0.01) { x.globalAlpha = menuMix; x.drawImage(sprites.bgMenu, 0, 0, W0, H0); x.globalAlpha = 1; }
      if (settings.quality === 'high' || settings.quality === 'mid') for (const [tx, ty, ph, sc] of twinkles) { const a = Math.max(0, Math.sin(now * 0.0012 * sc + ph)); if (a > 0.6) star4(x, tx, ty, 5 * sc, (a - 0.6) * 2); }
      if (menuMix > 0.05) {
        x.globalAlpha = menuMix;
        if (screen === 'start' || screen === 'win' || screen === 'lose' || menuMix < 0.98) for (const [fx0, fy0, c, s] of floaters) { const S = CS * SPR * s * 1.4; x.drawImage(sprites.gem[c], fx0 - S / 2, fy0 - S / 2 + Math.sin(now * 0.0013 + fx0) * 4, S, S); }
        if (screen === 'start' || (screen === 'game' && menuMix > 0.05)) drawHero(now);
        if (screen === 'win') drawRays(now);
        x.globalAlpha = 1;
      }
      const showBoard = g && (screen === 'game' || menuMix < 0.99);
      boardAlpha += ((screen === 'game' ? 1 : 0) - boardAlpha) * Math.min(1, dt * 8);
      if (showBoard && boardAlpha > 0.01) drawBoard(now, dt);
      x.globalAlpha = 1;
      drawParts();
      for (let q = popups.length - 1; q >= 0; q--) {
        const p = popups[q], e = (now - p.t0) / 950;
        if (e >= 1) { popups.splice(q, 1); continue; }
        const y = p.y - easeOut(e) * 26, a = e < 0.7 ? 1 : 1 - (e - 0.7) / 0.3, s = e < 0.12 ? 0.6 + e / 0.12 * 0.4 : 1;
        x.globalAlpha = a; x.font = `900 ${p.size * s}px ${FF}`; x.textAlign = 'center'; x.textBaseline = 'middle'; x.lineJoin = 'round';
        const text = '+' + fmt(p.pts); x.lineWidth = 4.5; x.strokeStyle = '#2a0b5c'; x.strokeText(text, p.x, y); x.fillStyle = p.size > 18 ? '#ffe066' : '#ffffff'; x.fillText(text, p.x, y);
      }
      x.globalAlpha = 1;
      if (flashA > 0.01) { x.fillStyle = `rgba(255,250,235,${flashA})`; x.fillRect(0, 0, W0, H0); }
    }
    function drawRays(now) {
      const cx = W0 / 2, cy = 70;
      x.save(); x.translate(cx, cy); x.rotate(now * 0.00025); x.globalCompositeOperation = 'lighter';
      for (let q = 0; q < 12; q++) { x.rotate(Math.PI / 6); const gr = x.createLinearGradient(0, 0, 0, -260); gr.addColorStop(0, 'rgba(255,210,90,.28)'); gr.addColorStop(1, 'rgba(255,210,90,0)'); x.fillStyle = gr; x.beginPath(); x.moveTo(0, 0); x.lineTo(-26, -260); x.lineTo(26, -260); x.closePath(); x.fill(); }
      x.restore();
    }
    function drawBoard(now, dt) {
      x.save();
      x.globalAlpha = boardAlpha;
      if (now < shakeUntil) { const a = shakeAmp * (shakeUntil - now) / shakeDur; x.translate(rnd(-a, a), rnd(-a, a)); } else shakeAmp = 0;
      x.drawImage(sprites.panel, 0, 0, W0, H0);
      // Лёд под кристаллами
      for (let i = 0; i < NN * NN; i++) {
        const o = cellObs[i];
        if (!o || !o.ice) continue;
        const [r, c] = rc(i); x.drawImage(sprites.ice[o.ice], BX + c * CS, BY + r * CS, CS, CS);
      }
      // Подсказка, наведение, выбор
      const cellRect = (i, fill, stroke, lw = 2) => { const [r, c] = rc(i); rr(x, BX + c * CS + 2, BY + r * CS + 2, CS - 4, CS - 4, 10); if (fill) { x.fillStyle = fill; x.fill(); } if (stroke) { x.strokeStyle = stroke; x.lineWidth = lw; x.stroke(); } };
      if (hint) { const a = 0.18 + 0.14 * Math.sin(now * 0.006); cellRect(hint[0], `rgba(255,240,170,${a})`); cellRect(hint[1], `rgba(255,240,170,${a})`); }
      if (hover >= 0 && !kbMode && hover !== sel && screen === 'game') cellRect(hover, 'rgba(255,255,255,.07)');
      if (sel >= 0) cellRect(sel, 'rgba(255,230,140,.16)');
      // Кристаллы (обрезка по полю: новые падают из-за верхнего края)
      x.save(); rr(x, BX - 2, BY - 2, BS + 4, BS + 4, 14); x.clip();
      let selView = null;
      for (const [id, v] of views) {
        viewPos(v, now);
        let cx = cellX(v.x), cy = cellY(v.y), sc = 1;
        if (v.die) {
          const p = (now - v.die) / 230;
          if (p >= 1 || (v.toward < 0 && p >= 0.42 && !v.shards)) {
            if (!v.shards && v.toward < 0) { v.shards = true; shatter(cx, cy, v.color >= 0 ? v.color : 6, v.kind === 'n' ? 1 : 1.6); }
            if (p >= 1 || v.toward < 0) { views.delete(id); continue; }
          }
          if (v.toward >= 0) { const [tr, tc] = rc(v.toward), e = easeInOut(clamp(p, 0, 1)); cx += (cellX(tc) - cx) * e; cy += (cellY(tr) - cy) * e; sc = 1 - 0.35 * e; x.globalAlpha = boardAlpha * (1 - e * 0.6); }
          else sc = 1 + 0.18 * (p / 0.42);
          drawPiece(v, cx, cy, sc, now);
          x.globalCompositeOperation = 'lighter'; x.globalAlpha = boardAlpha * Math.min(1, p * 2.5) * 0.8; const gs = CS * 1.3 * sc; x.drawImage(sprites.glow[v.color >= 0 ? v.color : 6], cx - gs / 2, cy - gs / 2, gs, gs);
          x.globalCompositeOperation = 'source-over'; x.globalAlpha = boardAlpha;
          continue;
        }
        if (v.born && now - v.born < 320) { const p = (now - v.born) / 320; sc = p < 0.6 ? 0.3 + p / 0.6 * 0.95 : 1.25 - (p - 0.6) / 0.4 * 0.25; }
        if (v.land && now - v.land < 160) { const p = (now - v.land) / 160; sc *= 1 - Math.sin(p * Math.PI) * 0.06; }
        if (hint && !v.tw && (rc(hint[0])[0] === v.y && rc(hint[0])[1] === v.x || rc(hint[1])[0] === v.y && rc(hint[1])[1] === v.x)) {
          const other = rc(hint[0])[0] === v.y && rc(hint[0])[1] === v.x ? hint[1] : hint[0], [or, oc] = rc(other), w = Math.sin(now * 0.012) * 0.07;
          cx += (oc - v.x) * CS * w; cy += (or - v.y) * CS * w;
        }
        const o = cellObs[Math.round(v.y) * NN + Math.round(v.x)];
        if (o && o.shake && now - o.shake < 260 && o.lock) cx += Math.sin((now - o.shake) * 0.09) * 3;
        const isSel = sel >= 0 && !v.tw && rc(sel)[0] === v.y && rc(sel)[1] === v.x;
        if (isSel) { selView = [v, cx, cy]; continue; }
        drawPiece(v, cx, cy, sc, now);
        if (v.flash && now - v.flash < 260) { x.globalCompositeOperation = 'lighter'; x.globalAlpha = boardAlpha * (1 - (now - v.flash) / 260); const gs = CS * 1.4; x.drawImage(sprites.glow[6], cx - gs / 2, cy - gs / 2, gs, gs); x.globalCompositeOperation = 'source-over'; x.globalAlpha = boardAlpha; }
        if (hover >= 0 && !kbMode && rc(hover)[0] === v.y && rc(hover)[1] === v.x && screen === 'game' && !busy()) { x.globalCompositeOperation = 'lighter'; x.globalAlpha = boardAlpha * 0.16; drawPiece(v, cx, cy, sc, now); x.globalCompositeOperation = 'source-over'; x.globalAlpha = boardAlpha; }
      }
      if (selView) { // выбранный кристалл приподнят, ярче и с контуром
        const [v, cx, cy] = selView, lift = 3 + Math.sin(now * 0.008) * 1.2;
        x.globalAlpha = boardAlpha * 0.35; x.fillStyle = '#000'; x.beginPath(); x.ellipse(cx, cy + CS * 0.36, CS * 0.3, CS * 0.08, 0, 0, 6.28); x.fill(); x.globalAlpha = boardAlpha;
        drawPiece(v, cx, cy - lift, 1.1, now);
        x.globalCompositeOperation = 'lighter'; x.globalAlpha = boardAlpha * 0.28; drawPiece(v, cx, cy - lift, 1.1, now); x.globalCompositeOperation = 'source-over'; x.globalAlpha = boardAlpha;
      }
      x.restore();
      if (sel >= 0) { x.save(); x.shadowColor = '#ffe27a'; x.shadowBlur = 10; cellRect(sel, null, `rgba(255,236,160,${0.75 + 0.25 * Math.sin(now * 0.008)})`, 2.4); x.restore(); }
      if (kbMode && screen === 'game') cellRect(cursor, null, `rgba(120,240,255,${0.7 + 0.3 * Math.sin(now * 0.01)})`, 2.6);
      // Цепи и камни поверх
      for (let i = 0; i < NN * NN; i++) {
        const o = cellObs[i]; if (!o) continue;
        const [r, c] = rc(i), sx = o.shake && now - o.shake < 220 ? Math.sin((now - o.shake) * 0.12) * 2.5 : 0;
        if (o.lock) x.drawImage(sprites.lock, BX + c * CS + sx, BY + r * CS, CS, CS);
        if (o.stone) x.drawImage(sprites.stone[o.stone], BX + c * CS + sx, BY + r * CS, CS, CS);
      }
      // Затемнение вокруг главного бонуса
      if (dimA > 0.01) {
        const [r, c] = rc(Math.max(0, dimCell)), cx = cellX(c), cy = cellY(r);
        const gr = x.createRadialGradient(cx, cy, CS * 0.6, cx, cy, CS * 3.2);
        gr.addColorStop(0, 'rgba(5,0,20,0)'); gr.addColorStop(1, `rgba(5,0,20,${dimA})`);
        x.fillStyle = gr; rr(x, BX - 7, BY - 7, BS + 14, BS + 14, 18); x.fill();
      }
      for (let q = fxList.length - 1; q >= 0; q--) { const f = fxList[q]; if (now > f.end) { fxList.splice(q, 1); continue; } }
      for (const layer of [1, 2]) for (const f of fxList) if (f.layer === layer) f.draw(now, dt);
      x.restore();
    }

    // ----- Цикл -----
    let lastHud = '';
    function frame(ts) {
      raf = 0;
      if (destroyed) return;
      frames++;
      if ((frames & 15) === 0) hot = calcHot();        // страховка: события фокуса и указателя могли прийти до смены состояния
      const rawDt = lastFrame ? Math.min(50, ts - lastFrame) : 16.7;
      lastFrame = ts;
      if (Math.abs(Math.min(3, window.devicePixelRatio || 1) - dpr) > 0.01) resizeCanvas();
      if (spritesDirty && !dragGrip) buildSprites();
      if (!sprites) buildSprites();
      timeScale = performance.now() < slowUntil ? 0.4 : 1;
      const dt = rawDt * timeScale * testSpeed;
      vis += dt;
      for (let q = timers.length - 1; q >= 0; q--) if (timers[q].at <= vis) { const t = timers[q]; timers.splice(q, 1); t.fn(); }
      if (g) updatePlayer();
      updateParts(dt / 1000);
      flashA *= Math.pow(0.0025, rawDt / 1000 * 2.4);
      if (vis > dimUntil) dimA *= Math.pow(0.02, rawDt / 1000 * 3);
      pollPad(ts);
      updateHint();
      draw(vis, dt / 1000);
      updateHud(rawDt);
      helpTick(vis); shopTick(vis);
      if (audio && soundOn) audio.tick();
      // Автоснижение плотности эффектов при долгих кадрах
      frameAvg += (rawDt - frameAvg) * 0.05;
      if (frameAvg > 26 && (settings.quality === 'high' || settings.quality === 'mid')) { if (++slowFrames > 120 && autoDrop < 2) { autoDrop++; slowFrames = 0; } } else slowFrames = 0;
      if (inst && inst.onFrame) inst.onFrame();
      schedule();
    }
    // Окно вне фокуса и без указателя не рисуется, пока на нём нечему доанимироваться: игра не отнимает процессор у встречи и других разделов.
    let hot = true;
    const settling = () => busy() || parts.length > 0 || fxList.length > 0 || timers.length > 0 || popups.length > 0 || vis < shakeUntil || flashA > 0.01;
    const calcHot = () => (win.matches(':focus-within') && document.hasFocus()) || win.matches(':hover');
    function updateHot() { hot = calcHot(); if (hot) wake(); }
    function schedule() { if (!raf && !destroyed && !minimized && !document.hidden && (hot || settling())) raf = requestAnimationFrame(frame); }
    function wake() { lastFrame = 0; schedule(); }
    on(document, 'visibilitychange', () => { if (!document.hidden) wake(); });
    for (const ev of ['focusin', 'focusout', 'pointerenter', 'pointerleave']) on(win, ev, updateHot);
    on(window, 'blur', updateHot); on(window, 'focus', updateHot);
    // Клавиши внутри окна игры не доходят до горячих клавиш приложения (Esc диалогов, «нажми и говори» и т. п.)
    for (const ev of ['keydown', 'keyup', 'keypress']) on(host, ev, (e) => { if (e.key !== 'Tab') e.stopPropagation(); });

    // ----- HUD -----
    const hud = { lv: $('.lv'), sc: $('.sc'), mv: $('.mv'), moves: $('.moves'), bar: $('.prog > i'), ps: $$('.prog .ps'), goals: $('.goals') };
    function goalList() {
      if (!g) return [];
      const left = (pred) => cellObs.reduce((n, o) => n + (o && pred(o) ? 1 : 0), 0);
      return ENG.goalState(g).map((G) => {
        let l = G.left;
        if (G.type === 'score') l = Math.max(0, G.target - disp.score);
        if (G.type === 'collect') l = Math.max(0, G.target - disp.collected[G.color]);
        if (G.type === 'ice') l = left((o) => o.ice > 0);
        if (G.type === 'locks') l = left((o) => o.lock > 0);
        if (G.type === 'stones') l = left((o) => o.stone > 0);
        return { ...G, left: l };
      });
    }
    function goalChip(G) {
      const I = getIcons();
      const src = G.type === 'collect' ? I.gem[G.color] : G.type === 'score' ? I.score : I[{ ice: 'ice', locks: 'lock', stones: 'stone' }[G.type]];
      const label = G.type === 'score' ? fmt(G.target) : String(G.left);
      const alt = G.type === 'score' ? 'Очки' : G.type === 'collect' ? 'Кристаллы: ' + PAL[G.color].name : { ice: 'Лёд', locks: 'Цепи', stones: 'Камни' }[G.type];
      return `<span class="goal${G.left === 0 ? ' done' : ''}"><img src="${src}" alt="${alt}"><b>${label}</b></span>`;
    }
    function updateHud(rawDt) {
      if (!g || screen !== 'game') return;
      if (shownScore < disp.score) shownScore = Math.min(disp.score, shownScore + Math.max(10, (disp.score - shownScore) * Math.min(1, rawDt / 90)));
      else shownScore = disp.score;
      const st = g.level.stars, goals = goalList(), mv = disp.moves;
      const key = [run.level, Math.round(shownScore), mv, goals.map((q) => q.left).join(',')].join('|');
      if (key === lastHud) return;
      lastHud = key;
      hud.lv.textContent = run.level;
      hud.sc.textContent = fmt(shownScore);
      hud.mv.textContent = mv;
      hud.moves.classList.toggle('low', mv <= 5 && mv > 0);
      hud.bar.style.width = `calc(${Math.min(100, shownScore / st[2] * 100)}% - 2px)`;
      hud.ps.forEach((el, q) => { el.style.left = (st[q] / st[2] * 100 - (q === 2 ? 3 : 0)) + '%'; const onNow = shownScore >= st[q]; if (onNow !== el.classList.contains('on')) el.classList.toggle('on', onNow); });
      hud.goals.innerHTML = goals.map(goalChip).join('');
    }

    // ----- Поздравления -----
    const PHR = {
      1: ['Отлично!', 'Молодец!', 'Супер!'],
      2: ['Красавчик!', 'Великолепно!', 'Превосходно!', 'Блестяще!'],
      3: ['Невероятно!', 'Вот это ход!', 'Потрясающе!', 'Шикарно!', 'Мастер кристаллов!'],
    };
    let lastPhrase = '';
    function praiseText(text, cls, px, py) {
      const el = document.createElement('div');
      el.className = 'praise ' + cls; el.textContent = text; el.dataset.t = text;
      if (cls !== 'info') for (const old of fxLayer.querySelectorAll('.praise:not(.info)')) old.remove(); // одна фраза за раз
      fxLayer.appendChild(el);
      const half = el.offsetWidth / u / 2 + 10; // фраза целиком внутри окна
      el.style.left = (clamp(px, half, W0 - half) * u) + 'px'; el.style.top = (py * u) + 'px';
      on(el, 'animationend', () => el.remove());
    }
    function praise(sum, cells) {
      const strong = sum.combos.some((c) => c[0] === 'm' || c === 'bb' || c === 'rb');
      let tier = 0;
      if (sum.waves >= 5 || sum.destroyed >= 30 || sum.combos.includes('mm') || (strong && sum.destroyed >= 20)) tier = 3;
      else if (sum.upgrades || sum.created.includes('b') || sum.created.includes('m') || sum.combos.length || sum.waves >= 3 || sum.destroyed >= 16) tier = 2;
      else if (sum.created.length || sum.waves >= 2) tier = 1;
      else if (Math.random() < 0.06) tier = 1; // за простые три — редко
      if (!tier) return;
      const list = PHR[tier].filter((p) => p !== lastPhrase);
      const text = list[Math.floor(Math.random() * list.length)];
      lastPhrase = text;
      // Рядом с комбинацией, но в пределах поля и не по центру поверх всего
      let px = W0 / 2, py = BY + BS * 0.42;
      if (cells && cells.length) { const [r, c] = rc(cells[0]); px = clamp(cellX(c), 130, W0 - 130); py = clamp(cellY(r) - CS * 0.9, BY + 40, BY + BS - 50); }
      praiseText(text, 't' + tier, px, py);
      sfx('praise', tier);
      if (tier === 3) { sparks(px, py, 7, 40, 260, 0.9, 22); sparks(px, py, 4, 20, 200, 0.8, 18); flash(0.18); }
      live.textContent = text;
    }

    // ----- Сюжет: пять маяков Хрустальной долины -----
    const CHAPTERS = ['Маяк Рассвета', 'Ледяной маяк', 'Маяк в цепях', 'Каменный маяк', 'Маяк Морока'];
    const STORY = [
      'Морок погасил пять маяков Хрустальной долины. Хранительница Искра просит собрать свет кристаллов.',
      'По стеклу маяка побежали первые огоньки. Ещё немного света!',
      'Из темноты выглядывают рубины — Искре нужно именно их тепло.',
      'Последний рывок — и маяк Рассвета вспыхнет над долиной.',
      'Морок сковал второй маяк льдом. Растопи его светом кристаллов.',
      'Под ледяной коркой спят кристаллы. Разбуди их!',
      'Здесь лёд толще: Морок старался. Бей дважды.',
      'Цепи Морока держат последние кристаллы ледяного маяка.',
      'Третий маяк обмотан цепями и завален камнями.',
      'Искра нашла старую кладовую: лёд и фиолетовые аметисты.',
      'Цепи на льду — хитрая ловушка. Попробуй собрать супер-ракету.',
      'Камни сложены ромбом — это печать Морока. Сломай её.',
      'Четвёртый маяк сложен из живого камня. Ему нужны три цвета света.',
      'Глубокий лёд у подножия каменного маяка.',
      'Маяк просыпается — дай ему как можно больше света!',
      'Цепи и камни переплелись. Пропеллеры найдут, где тоньше.',
      'Логово Морока: ледяная рамка и цепи вокруг последнего маяка.',
      'Морок слабеет! Держи свет и не давай льду вернуться.',
      'Последние камни-стражи. За ними — сердце маяка.',
      'Финальная битва за свет. Зажги маяк Морока — и долина засияет!',
    ];
    const chapterOf = (n) => (n <= 20 ? Math.floor((n - 1) / 4) : -1);
    const storyOf = (n) => (n <= 20 ? STORY[n - 1] : `Бесконечные пещеры, зал ${n - 20}. Морок прячется всё глубже.`);
    const beaconsLit = (n) => Math.min(5, Math.floor(n / 4)); // после победы на уровне n
    const SHOP = [
      { k: 'r', name: 'Ракета', text: 'Чистит строку или столбец' },
      { k: 'p', name: 'Пропеллер', text: 'Сам летит к самой нужной цели' },
      { k: 'b', name: 'Бомба', text: 'Взрыв 5×5' },
      { k: 'm', name: 'Мегакристалл', text: 'Убирает целый цвет' },
      { k: 'moves', name: '+5 ходов', text: 'Больше времени на цель' },
    ];

    // ----- Игровой процесс -----
    let run = null, introCardTimer = null;
    const card = $('.card');
    const bestNow = () => store.get('best', 0);
    function total() { return run ? run.banked + (g ? g.score : 0) : 0; }
    function updateBest() { const t = total(); if (t > bestNow()) store.set('best', t); }
    function endRun() {
      if (!run) return;
      const t = total();
      updateBest();
      if (t > 0) { const recs = store.get('records', []); recs.push({ s: t, l: run.level, d: Date.now() }); recs.sort((a, b) => b.s - a.s); store.set('records', recs.slice(0, 5)); }
      run = null;
    }
    // Осколки и купленные бонусы живут только в партии: закрыл окно — всё сгорело, как и сама партия
    function startRun() { run = { banked: 0, level: 1, seed: 0, coins: 0, boost: {}, cart: {}, earned: 0 }; newLevel(1); }
    function newLevel(n) { run.level = n; run.seed = (Math.random() * 2147483647) | 0; beginLevel(); }
    function beginLevel() {
      g = ENG.createGame(run.level, run.seed, run.boost); // повтор уровня — с теми же купленными бонусами
      queue.length = 0; cur = null; fxList.length = 0; popups.length = 0; sel = -1; hint = null; hover = -1;
      const introDur = syncFromEngine(true);
      shownScore = 0; lastHud = '';
      show('game');
      enqueue({ type: 'wait', dur: reduce() ? 200 : introDur });
      showCard();
      lastInput = vis;
      win.focus({ preventScroll: true });
    }
    function showCard() {
      const L = g.level;
      const goals = ENG.goalState(g);
      const desc = goals.map((G) => G.type === 'score' ? `набери ${fmt(G.target)} очков` : G.type === 'collect' ? `собери ${G.target} ${PAL[G.color].name}` : G.type === 'ice' ? 'разбей весь лёд' : G.type === 'locks' ? 'сними все цепи' : 'разбей все камни').join(', ');
      const ch = chapterOf(run.level), bought = Object.values(run.boost || {}).reduce((a, b) => a + b, 0);
      card.innerHTML = `<div class="chap">${ch >= 0 ? `Глава ${ch + 1} · ${CHAPTERS[ch]}` : 'Бесконечные пещеры'}</div><h2>Уровень ${run.level}</h2><p class="story">${storyOf(run.level)}</p><div class="cg">${goals.map(goalChip).join('')}</div><p class="ct">${desc[0].toUpperCase() + desc.slice(1)} за ${g.moves} ${plural(g.moves, 'ход', 'хода', 'ходов')}.${L.tip ? '<br>' + L.tip : ''}${bought ? '<br>Твои покупки уже на поле!' : ''}</p><div class="cgo">Нажми, чтобы начать</div>`;
      card.classList.remove('off');
      introCardTimer = vis + 5200;
      live.textContent = `Уровень ${run.level}. ${desc}.`;
    }
    function hideCard() { if (introCardTimer !== null) { card.classList.add('off'); introCardTimer = null; } }
    const plural = (n, a, b, c) => { const m = n % 100, l = n % 10; return m >= 11 && m <= 14 ? c : l === 1 ? a : l >= 2 && l <= 4 ? b : c; };
    on(card, 'pointerdown', (e) => { e.stopPropagation(); hideCard(); });

    function attempt(a, b) {
      if (busy() || !g || g.won || g.lost) return;
      hint = null; sel = -1;
      const res = ENG.trySwap(g, a, b);
      if (!res.valid) {
        if (res.bounce) enqueue({ type: 'swap', a, b, ida: res.ida, idb: res.idb, bounce: true });
        else if (res.blocked) { for (const i of [a, b]) if (cellObs[i] && cellObs[i].lock) cellObs[i].shake = vis; sfx('bad'); }
        return;
      }
      disp.moves = g.moves;
      // Поздравление — в момент главного события: сразу после комбинации бонусов или на третьей волне каскада
      const sum = res.summary, early = sum.combos.length || sum.created.includes('b') || sum.created.includes('m');
      const at = early ? 0 : Math.min(2, sum.waves - 1);
      const steps = res.steps.slice();
      let wi = -1, pos = steps.length;
      for (let q = 0; q < steps.length; q++) if (steps[q].type === 'wave' && ++wi === at) { pos = q + 1; break; }
      const w0 = steps.find((st) => st.type === 'wave'), m0 = w0 && w0.events.find((e) => e.type === 'match');
      const cells = m0 ? m0.cells : [b];
      steps.splice(pos, 0, { type: 'call', fn: () => praise(sum, cells) });
      enqueue(...steps, { type: 'call', fn: () => afterMove() });
    }
    function afterMove() {
      reconcile();
      updateBest();
      if (g.won) {
        enqueue({ type: 'wait', dur: 450 });
        if (g.moves > 0) {
          enqueue({ type: 'wait', dur: 500 }, { type: 'call', fn: () => { praiseText('Бонус за ходы!', 'info', W0 / 2, BY + BS * 0.78); } }, { type: 'wait', dur: 400 });
          const f = ENG.finale(g);
          enqueue(...f.steps, { type: 'wait', dur: 400 });
        }
        enqueue({ type: 'call', fn: () => { reconcile(); updateBest(); showWin(); } });
      } else if (g.lost) enqueue({ type: 'wait', dur: 500 }, { type: 'call', fn: showLose });
    }
    const WIN_PHR = {
      1: ['Уровень позади — вперёд!', 'Получилось! Дальше будет интереснее', 'Есть! Попробуй собрать больше звёзд'],
      2: ['Отличная игра!', 'Здорово получилось!', 'Ещё немного — и три звезды!'],
      3: ['Ты настоящий мастер кристалликов!', 'Безупречно! Три звезды!', 'Великолепная игра!'],
    };
    function showWin() {
      const stars = ENG.starsFor(g);
      $('.ws').textContent = fmt(g.score);
      $('.wtot').textContent = run.level > 1 || run.banked ? `Всего за игру: ${fmt(total())}` : '';
      const ph = WIN_PHR[stars];
      const lit = run.level <= 20 && run.level % 4 === 0;
      $('.phrase').textContent = lit ? `${CHAPTERS[chapterOf(run.level)]} зажжён! Свет возвращается в долину.` : ph[Math.floor(Math.random() * ph.length)];
      run.earned = ENG.CONFIG.shop.coins(stars, g.score);
      run.coins += run.earned;
      $('.wcoins').innerHTML = `<img src="${getIcons().gem[4]}" alt=""> +${run.earned} ${plural(run.earned, 'осколок', 'осколка', 'осколков')}`;
      const big = $$('.stars .big');
      big.forEach((el) => { el.classList.remove('in', 'got'); el.style.opacity = '0'; });
      show('win');
      sfx('win');
      const pos = [[57, 75], [151, 46], [243, 75]];
      big.forEach((el, q) => after(350 + q * 330, () => {
        el.style.opacity = ''; el.classList.toggle('got', q < stars); el.classList.add('in');
        if (q < stars) { const [sx, sy] = pos[q]; sparks(sx + 70, sy + 18, 7, 34, 240, 0.9, 20); for (let s = 0; s < 8 * qf(); s++) emit(2, sx + 70, sy + 18, rnd(-120, 120), rnd(-150, 20), 0.9, rnd(6, 11), 6, 200); sfx('star', q); }
      }));
      live.textContent = `Уровень пройден! Звёзд: ${stars}. Очки: ${fmt(g.score)}.`;
    }
    function showLose() {
      $('.ls').textContent = fmt(g.score);
      const left = ENG.goalState(g).filter((G) => G.left > 0);
      $('.left').innerHTML = left.map(goalChip).join('');
      $('.lp').textContent = left.length && left.every((G) => G.left <= Math.max(2, G.target * 0.15)) ? 'Совсем чуть-чуть не хватило!' : 'Попробуй ещё раз: бонусы помогут';
      show('lose'); sfx('lose');
      live.textContent = `Ходы закончились. Очки: ${fmt(g.score)}.`;
    }

    // ----- Экраны -----
    const screens = { start: $('.start'), game: $('.game'), win: $('.victory'), lose: $('.lose'), shop: $('.shop'), help: $('.help'), settings: $('.settings'), records: $('.records') };
    let backTo = 'start';
    function show(name) {
      if ((name === 'help' || name === 'settings' || name === 'records') && screen !== name) backTo = screen === 'help' || screen === 'settings' || screen === 'records' ? backTo : screen;
      screen = name;
      for (const [n, el] of Object.entries(screens)) el.classList.toggle('off', n !== name);
      if (name !== 'game') { hideCard(); sel = -1; hint = null; fxLayer.replaceChildren(); }
      if (name === 'start') { $('.bestv').textContent = fmt(bestNow()); focusLater('.play'); }
      if (name === 'win') focusLater('.next');
      if (name === 'lose') focusLater('.again');
      if (name === 'shop') { syncShop(); focusLater('.go'); }
      if (name === 'help') { buildHelp(); focusLater('.help .back'); }
      if (name === 'settings') { syncSettings(); focusLater('.settings .back'); }
      if (name === 'records') { buildRecords(); focusLater('.records .back'); }
      if (name === 'game') win.focus({ preventScroll: true });
    }
    const focusLater = (s) => after(30, () => { const el = $(s); if (el && !destroyed) el.focus({ preventScroll: true }); });
    on($('.play'), 'click', () => { sfx('click'); startRun(); });
    on($('.next'), 'click', () => { sfx('click'); run.banked += g.score; g = null; run.cart = {}; show('shop'); });
    on($('.go'), 'click', () => { sfx('click'); run.boost = run.cart; run.cart = {}; newLevel(run.level + 1); });
    for (const el of $$('.item')) on(el, 'click', () => buy(el.dataset.k));
    function buy(kk) {
      const price = ENG.CONFIG.shop.prices[kk], have = run.cart[kk] || 0;
      if (run.coins < price || have >= ENG.CONFIG.shop.max) { sfx('bad'); el$(kk).classList.add('no'); after(400, () => el$(kk).classList.remove('no')); return; }
      run.coins -= price; run.cart[kk] = have + 1; sfx('create'); syncShop();
      const [ix, iy] = shopPos(kk); sparks(ix, iy, 7, 18, 160, 0.6, 16);
    }
    const el$ = (kk) => $(`.item[data-k="${kk}"]`);
    function shopPos(kk) { const el = el$(kk), r0 = el.getBoundingClientRect(), rb = cv.getBoundingClientRect(); return [(r0.left - rb.left + r0.width / 2) / u, (r0.top - rb.top + 28 * u) / u]; }
    function syncShop() {
      if (!run) return;
      $('.cv').textContent = run.coins;
      for (const im of $$('img.coin')) im.src = getIcons().gem[4];
      const lit = beaconsLit(run.level);
      $('.beacons').innerHTML = (run.level <= 20 ? CHAPTERS.map((n, q) => `<i class="${q < lit ? 'lit' : ''}" title="${n}"></i>`).join('') + `<span>${lit === 5 ? 'Все маяки горят!' : `Маяков зажжено: ${lit} из 5`}</span>` : `<span>Бесконечные пещеры, зал ${run.level - 19}</span>`);
      for (const it of SHOP) {
        const el = el$(it.k), have = run.cart[it.k] || 0, price = ENG.CONFIG.shop.prices[it.k];
        el.querySelector('.cnt').textContent = have ? '×' + have : '';
        el.classList.toggle('poor', run.coins < price || have >= ENG.CONFIG.shop.max);
        el.setAttribute('aria-label', `${it.name}: ${it.text}. Цена ${price}. Куплено ${have}`);
      }
    }
    on($('.again'), 'click', () => { sfx('click'); beginLevel(); });
    for (const el of $$('[data-act="retry"]')) on(el, 'click', () => { sfx('click'); beginLevel(); });
    for (const el of $$('[data-act="home"]')) on(el, 'click', () => { sfx('click'); endRun(); g = null; show('start'); });
    for (const el of $$('[data-go]')) on(el, 'click', () => { sfx('click'); show(el.dataset.go); });
    for (const el of $$('.back')) on(el, 'click', () => { sfx('click'); show(backTo === 'game' && !g ? 'start' : backTo); });

    function syncSettings() {
      $$('.seg.q button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.q === settings.quality)));
      $('.sw.rm').setAttribute('aria-checked', String(settings.reduce));
      $('.vm').value = Math.round(settings.music * 100); $('.vmo').textContent = Math.round(settings.music * 100) + '%';
      $('.vs').value = Math.round(settings.sfx * 100); $('.vso').textContent = Math.round(settings.sfx * 100) + '%';
      win.classList.toggle('reduce', settings.reduce);
    }
    for (const b of $$('.seg.q button')) on(b, 'click', () => { settings.quality = b.dataset.q; autoDrop = 0; saveSettings(); syncSettings(); resizeCanvas(); sfx('click'); });
    on($('.sw.rm'), 'click', () => { settings.reduce = !settings.reduce; saveSettings(); syncSettings(); sfx('click'); });
    for (const [sel, key] of [['.vm', 'music'], ['.vs', 'sfx']]) on($(sel), 'input', (e) => {
      settings[key] = +e.target.value / 100; saveSettings(); syncSettings();
      if (audio) audio.volumes(settings.music, settings.sfx);
    });
    on($('.vs'), 'change', () => sfx('match', 3, 1));
    syncSettings();

    function buildRecords() {
      const recs = store.get('records', []);
      $('.recs').innerHTML = recs.length ? recs.map((r) => `<li><span class="rs">${fmt(r.s)}</span><span class="rl">уровень ${r.l}<br>${new Date(r.d).toLocaleDateString('ru-RU')}</span></li>`).join('')
        : '<p class="empty">Здесь появятся пять лучших партий. Сыграй первую!</p>';
    }

    // ----- Справка с живыми иллюстрациями -----
    let helpCanv = [];
    function buildHelp() {
      const sc = $('.help .scroll');
      if (sc.dataset.built) { sc.scrollTop = 0; return; }
      const row = (id, title, text) => `<div class="hrow"><canvas data-h="${id}"></canvas><div><b>${title}</b><span>${text}</span></div></div>`;
      sc.innerHTML = `
        <div class="sec"><h3>Правила</h3>
          <p>Меняй местами два соседних кристалла по горизонтали или вертикали. Три и больше одинаковых в линию или квадратом 2×2 исчезают, сверху падают новые.</p>
          <p>Каждая перестановка — один ход. Выполни цель уровня, пока ходы не кончились. Неудачная перестановка ход не тратит.</p>
          ${row('m3', 'Три в ряд', 'Обычная комбинация: 100 очков за кристалл.')}
        </div>
        <div class="sec"><h3>Особые кристаллы</h3>
          ${row('rocket', '4 в линию — ракета', 'Очищает всю строку или весь столбец — в ту же сторону, в какую лежала четвёрка.')}
          ${row('prop', 'Квадрат 2×2 — пропеллер', 'Сбивает соседей крестом, взлетает и бьёт самую полезную цель: лёд, цепь, камень или нужный цвет.')}
          ${row('bomb', '5 буквой Т или Г — бомба', 'Взрывает квадрат 5×5 вокруг себя.')}
          ${row('mega', '5 в линию — мегакристалл', 'Сдвинь на любой кристалл — исчезнут все кристаллы этого цвета.')}
          <p>Бонус срабатывает, если сдвинуть его на соседнюю клетку, собрать в комбинацию или задеть другим бонусом — тогда начинается цепная реакция.</p>
        </div>
        <div class="sec"><h3>Комбинации бонусов</h3>
          <p>Поменяй местами два бонуса — они сработают вместе.</p>
          ${row('rr', 'Ракета + ракета', '«Звезда»: строка, столбец и обе диагонали.')}
          ${row('rb', 'Ракета + бомба', 'Три строки и три столбца.')}
          ${row('bb', 'Бомба + бомба', 'Огромный взрыв 7×7.')}
          ${row('pp', 'Пропеллер + пропеллер', 'Три пропеллера летят к трём целям.')}
          ${row('pr', 'Пропеллер + ракета', 'Пропеллер уносит ракету к цели и запускает её там.')}
          ${row('pb', 'Пропеллер + бомба', 'Пропеллер доставляет бомбу к цели.')}
          ${row('mr', 'Мегакристалл + ракета', 'Все кристаллы цвета ракеты становятся ракетами и стреляют.')}
          ${row('mb', 'Мегакристалл + бомба', 'Все кристаллы цвета бомбы становятся бомбами.')}
          ${row('mp', 'Мегакристалл + пропеллер', 'Целая эскадрилья пропеллеров.')}
          ${row('mm', 'Два мегакристалла', 'Очищают всё поле.')}
        </div>
        <div class="sec"><h3>Превращение — супер-бонусы</h3>
          <p>Собери новый бонус так, чтобы в фигуру попал уже стоящий бонус того же вида: старый не взорвётся, а вольётся в новый. Получится супер-бонус с золотым сиянием.</p>
          ${row('up', 'Супер-ракета', 'Ракета внутри новой четвёрки. Чистит три линии сразу.')}
          ${row('upb', 'Супер-бомба', 'Бомба внутри новой фигуры Т или Г. Взрыв 7×7.')}
          <p>Супер-пропеллер на месте цели взрывает квадрат 3×3. В сочетаниях каждый супер-бонус добавляет мощи: шире линии, больше взрыв, больше пропеллеров.</p>
        </div>
        <div class="sec"><h3>Лавка и маяки</h3>
          <p>За каждый пройденный уровень дают осколки, и чем больше звёзд и очков, тем их больше. После уровня открывается лавка: ракета, пропеллер, бомба, мегакристалл или +5 ходов. Купленное появится на поле следующего уровня в случайных клетках.</p>
          <p>Каждые четыре уровня зажигают один из пяти маяков Хрустальной долины. Осколки и покупки живут до закрытия окна.</p>
        </div>
        <div class="sec"><h3>Препятствия</h3>
          ${row('ice', 'Лёд', 'Лежит под кристаллом. Трескается, когда кристалл над ним исчезает. Двойной лёд — в два удара.')}
          ${row('lock', 'Цепь', 'Кристалл в цепи нельзя двигать. Собери его в комбинацию или задень бонусом — цепь спадёт.')}
          ${row('stone', 'Камень', 'Занимает клетку. Разбивается соседней комбинацией или бонусом. Крепкий — в два удара.')}
        </div>
        <div class="sec"><h3>Очки и звёзды</h3>
          <p>100 очков за кристалл, больше — за лёд, цепи, камни и создание бонусов. Каждая следующая волна каскада дороже: ×1,5, ×2, ×2,5… Оставшиеся ходы в конце уровня превращаются в ракеты.</p>
          <p>Звёзды даются за очки уровня: первая — за выполненную цель.</p>
        </div>
        <div class="sec"><h3>Управление</h3>
          <div class="keys">
            <kbd>Мышь, палец</kbd><span>перетащи кристалл на соседнюю клетку или нажми на два соседних по очереди</span>
            <kbd>Стрелки</kbd><span>выбор клетки; со взятым кристаллом — ход в эту сторону</span>
            <kbd>Enter, пробел</kbd><span>взять или поставить кристалл</span>
            <kbd>Esc</kbd><span>отменить выбор, назад</span>
            <kbd>Геймпад</kbd><span>крестовина и стик — выбор, A — взять, B — отмена</span>
            <kbd>Alt + M</kbd><span>открыть игру</span>
          </div>
        </div>
        <p class="author">Автор: Calleograph</p>`;
      sc.dataset.built = '1';
      helpCanv = [...sc.querySelectorAll('canvas[data-h]')].map((c) => ({ c, id: c.dataset.h, w: 0 }));
    }
    function shopTick(now) {
      if (screen !== 'shop') return;
      for (const c of $$('.items canvas')) {
        const pw = Math.round(54 * k);
        if (c.width !== pw) { c.width = pw; c.height = pw; }
        const hx = c.getContext('2d'); hx.setTransform(k, 0, 0, k, 0, 0); hx.clearRect(0, 0, 54, 54);
        drawHelp(hx, c.dataset.h, now);
      }
    }
    function helpTick(now) {
      if (screen !== 'help' || !helpCanv.length) return;
      const sc = $('.help .scroll'), top = sc.scrollTop, hgt = sc.clientHeight;
      for (const h of helpCanv) {
        const pw = Math.round(136 * k), ph = Math.round(56 * k);
        if (h.c.width !== pw) { h.c.width = pw; h.c.height = ph; }
        const y = h.c.offsetTop - sc.offsetTop;
        if (y + h.c.offsetHeight < top - 20 || y > top + hgt + 20) continue; // рисуем только видимые
        const hx = h.c.getContext('2d'); hx.setTransform(k, 0, 0, k, 0, 0); hx.clearRect(0, 0, 136, 56);
        drawHelp(hx, h.id, now);
      }
    }
    function drawHelp(hx, id, now) {
      const prev = x; x = hx; // те же функции отрисовки, другой холст
      const gem = (c, px, py, s = 0.42) => { const S = CS * SPR * s; x.drawImage(sprites.gem[c], px - S / 2, py - S / 2, S, S); };
      const piece = (kind, c, px, py, s = 0.62) => drawPiece({ kind, color: c, ph: 0.3 }, px, py, s, now);
      const arrow = (px) => { x.fillStyle = '#ffd84a'; x.beginPath(); x.moveTo(px, 22); x.lineTo(px + 10, 28); x.lineTo(px, 34); x.closePath(); x.fill(); };
      const plus = (px) => { x.fillStyle = '#ffd84a'; x.fillRect(px - 5, 26.5, 10, 3); x.fillRect(px - 1.5, 23, 3, 10); };
      const mini = (pred, px = 92) => { // мини-поле 5×5 с подсветкой зоны поражения
        const s = 8.4, pulse = 0.55 + 0.45 * Math.sin(now * 0.006);
        for (let r = 0; r < 5; r++) for (let c = 0; c < 5; c++) {
          const hit = pred(r, c);
          x.fillStyle = hit ? `rgba(255,214,90,${0.35 + 0.55 * pulse})` : 'rgba(140,120,230,.25)';
          x.fillRect(px + c * (s + 1), 5 + r * (s + 1), s, s);
        }
      };
      const loop = (now % 2400) / 2400;
      switch (id) {
        case 'm3': { const g0 = loop < 0.5; for (let q = 0; q < 3; q++) gem(0, 24 + q * 26, 28, g0 ? 0.42 : 0.42 * Math.max(0, 1 - (loop - 0.5) * 4)); gem(1, 102, 28); gem(3, 124, 28, 0.3); break; }
        case 'rocket': for (let q = 0; q < 4; q++) gem(1, 12 + q * 18, 28, 0.32); arrow(82); piece('rh', 1, 114, 28, 0.62); break;
        case 'prop': for (let q = 0; q < 4; q++) gem(3, 18 + (q % 2) * 20, 18 + Math.floor(q / 2) * 20, 0.3); arrow(66); piece('p', 3, 104, 28, 0.6); break;
        case 'bomb': for (let q = 0; q < 3; q++) gem(4, 10 + q * 17, 12, 0.27); gem(4, 27, 29, 0.27); gem(4, 27, 46, 0.27); arrow(64); piece('b', 4, 104, 29, 0.6); break;
        case 'mega': for (let q = 0; q < 5; q++) gem(0, 8 + q * 14, 28, 0.25); arrow(78); drawMega(112, 28, 15, now); break;
        case 'rr': piece('rh', 0, 16, 28, 0.42); plus(38); piece('rv', 2, 58, 28, 0.42); mini((r, c) => r === 2 || c === 2 || r === c || r + c === 4); break;
        case 'rb': piece('rh', 0, 16, 28, 0.42); plus(38); piece('b', 4, 58, 28, 0.42); mini((r, c) => Math.abs(r - 2) <= 1 || Math.abs(c - 2) <= 1); break;
        case 'bb': piece('b', 1, 16, 28, 0.42); plus(38); piece('b', 4, 58, 28, 0.42); mini(() => true); break;
        case 'pp': piece('p', 3, 16, 28, 0.42); plus(38); piece('p', 5, 58, 28, 0.42); mini((r, c) => (r === 2 && Math.abs(c - 2) <= 1) || (c === 2 && Math.abs(r - 2) <= 1) || (r === 0 && c === 4) || (r === 4 && c === 0) || (r === 0 && c === 0)); break;
        case 'pr': piece('p', 3, 16, 28, 0.42); plus(38); piece('rh', 0, 58, 28, 0.42); mini((r, c) => r === 0); break;
        case 'pb': piece('p', 3, 16, 28, 0.42); plus(38); piece('b', 4, 58, 28, 0.42); mini((r, c) => r <= 2 && c >= 2); break;
        case 'mr': drawMega(16, 28, 11, now); plus(38); piece('rh', 0, 58, 28, 0.42); arrow(78); piece('rh', 0, 104, 18, 0.34); piece('rv', 0, 124, 38, 0.34); piece('rh', 0, 104, 42, 0.3); break;
        case 'mb': drawMega(16, 28, 11, now); plus(38); piece('b', 4, 58, 28, 0.42); arrow(78); piece('b', 4, 102, 20, 0.34); piece('b', 4, 124, 36, 0.34); break;
        case 'mp': drawMega(16, 28, 11, now); plus(38); piece('p', 2, 58, 28, 0.42); arrow(78); piece('p', 2, 100, 16, 0.3); piece('p', 2, 122, 26, 0.3); piece('p', 2, 104, 42, 0.3); break;
        case 'mm': drawMega(16, 28, 11, now); plus(38); drawMega(58, 28, 11, now + 500); mini(() => true); break;
        case 'ice': x.drawImage(sprites.ice[2], 10, 6, 44, 44); gem(5, 32, 28, 0.36); x.drawImage(sprites.ice[1], 76, 6, 44, 44); gem(2, 98, 28, 0.36); break;
        case 'lock': gem(4, 40, 28, 0.38); x.drawImage(sprites.lock, 18, 6, 44, 44); arrow(76); gem(4, 110, 28, 0.38); break;
        case 'stone': x.drawImage(sprites.stone[2], 14, 6, 44, 44); arrow(68); x.drawImage(sprites.stone[1], 88, 6, 44, 44); break;
        case 'ir': piece('rh', 1, 27, 27, 0.78); break;
        case 'ip': piece('p', 3, 27, 27, 0.74); break;
        case 'ib': piece('b', 4, 27, 29, 0.74); break;
        case 'im': drawMega(27, 27, 19, now); break;
        case 'imoves': gem(5, 27, 27, 0.66); x.font = `900 20px ${FF}`; x.textAlign = 'center'; x.textBaseline = 'middle'; x.lineWidth = 4; x.strokeStyle = '#2a0b5c'; x.strokeText('+5', 27, 29); x.fillStyle = '#fff'; x.fillText('+5', 27, 29); break;
        case 'up': piece('rh', 0, 16, 28, 0.42); plus(36); gem(0, 52, 28, 0.3); gem(0, 64, 28, 0.3); gem(0, 76, 28, 0.3); arrow(88); drawPiece({ kind: 'rh', color: 0, ph: 0.3, up: 1 }, 118, 28, 0.5, now); break;
        case 'upb': piece('b', 4, 16, 28, 0.42); plus(36); gem(4, 52, 22, 0.26); gem(4, 64, 22, 0.26); gem(4, 64, 34, 0.26); arrow(88); drawPiece({ kind: 'b', color: 4, ph: 0.3, up: 1 }, 118, 28, 0.5, now); break;
      }
      x = prev;
    }

    // ----- Ввод: мышь, касание, клавиатура, геймпад -----
    const toCell = (e) => {
      const r = cv.getBoundingClientRect(), lx = (e.clientX - r.left) / u, ly = (e.clientY - r.top) / u;
      const c = Math.floor((lx - BX) / CS), rr0 = Math.floor((ly - BY) / CS);
      return c >= 0 && c < NN && rr0 >= 0 && rr0 < NN ? rr0 * NN + c : -1;
    };
    let drag = null;
    function interact() { lastInput = vis; hint = null; }
    function clickCell(i) {
      if (i < 0 || !g) return;
      if (sel < 0) { if (g.cells[i].piece) { sel = i; sfx('select'); } return; }
      if (sel === i) { sel = -1; return; }
      if (ENG.adjacent(NN, sel, i) && !busy()) { attempt(sel, i); return; }
      sel = g.cells[i].piece ? i : -1;
      if (sel >= 0) sfx('select');
    }
    on(cv, 'pointerdown', (e) => {
      if (screen !== 'game' || e.button > 0) return;
      win.focus({ preventScroll: true });
      interact(); kbMode = false;
      if (introCardTimer !== null) { hideCard(); return; }
      const i = toCell(e);
      if (i < 0) { sel = -1; return; }
      drag = { id: e.pointerId, i, x0: e.clientX, y0: e.clientY, done: false };
      capture(cv, e);
      e.preventDefault();
    });
    on(cv, 'pointermove', (e) => {
      if (screen !== 'game') return;
      hover = e.pointerType === 'mouse' ? toCell(e) : -1;
      if (!drag || e.pointerId !== drag.id || drag.done) return;
      const dx = (e.clientX - drag.x0) / u, dy = (e.clientY - drag.y0) / u;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < CS * 0.32) return;
      drag.done = true;
      const [r, c] = rc(drag.i);
      const tr = Math.abs(dx) > Math.abs(dy) ? r : r + Math.sign(dy), tc = Math.abs(dx) > Math.abs(dy) ? c + Math.sign(dx) : c;
      if (tr >= 0 && tr < NN && tc >= 0 && tc < NN) { sel = -1; attempt(drag.i, tr * NN + tc); }
    });
    const up = (e) => { if (!drag || e.pointerId !== drag.id) return; if (!drag.done && e.type === 'pointerup') clickCell(drag.i); drag = null; };
    on(cv, 'pointerup', up); on(cv, 'pointercancel', up);
    on(cv, 'pointerleave', () => { hover = -1; });

    function moveCursor(dr, dc) {
      const [r, c] = rc(cursor), nr = clamp(r + dr, 0, NN - 1), nc = clamp(c + dc, 0, NN - 1);
      if (sel >= 0) { const [sr, sc0] = rc(sel), tr = sr + dr, tc = sc0 + dc; if (tr >= 0 && tr < NN && tc >= 0 && tc < NN) { const from = sel; cursor = tr * NN + tc; attempt(from, cursor); } return; }
      cursor = nr * NN + nc;
    }
    function confirmKey() {
      if (introCardTimer !== null) { hideCard(); return; }
      clickCell(cursor);
    }
    on(win, 'keydown', (e) => {
      if (e.ctrlKey || e.altKey || e.metaKey) return; // горячие клавиши приложения не трогаем
      const onButton = e.target.closest && e.target.closest('button');
      if (e.key === 'Escape') {
        if (screen === 'game') { if (sel >= 0) sel = -1; else hideCard(); }
        else if (screen === 'help' || screen === 'settings' || screen === 'records') show(backTo === 'game' && !g ? 'start' : backTo);
        e.preventDefault(); e.stopPropagation(); return;
      }
      if (screen !== 'game' || onButton && (e.key === 'Enter' || e.key === ' ')) return;
      const dirs = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
      if (dirs[e.key]) { interact(); if (introCardTimer !== null) hideCard(); kbMode = true; moveCursor(...dirs[e.key]); }
      else if (e.key === 'Enter' || e.key === ' ') { interact(); kbMode = true; confirmKey(); }
      else return;
      e.preventDefault(); e.stopPropagation();
    });

    const pad = { prev: {}, rep: {} };
    function pollPad(ts) {
      let pads = [];
      try { pads = navigator.getGamepads ? navigator.getGamepads() : []; } catch (e) { return; }
      let gp = null;
      for (const p of pads) if (p && p.connected) { gp = p; break; } // первый подключённый
      if (!gp) return;
      const b = (n) => !!(gp.buttons[n] && gp.buttons[n].pressed), ax = gp.axes[0] || 0, ay = gp.axes[1] || 0;
      const st = { up: b(12) || ay < -0.55, down: b(13) || ay > 0.55, left: b(14) || ax < -0.55, right: b(15) || ax > 0.55, a: b(0), b: b(1), start: b(9) };
      const press = (n) => { const now = st[n], was = pad.prev[n]; if (now && !was) { pad.rep[n] = ts + 320; return true; } if (now && was && ts > pad.rep[n] && 'updownleftright'.includes(n)) { pad.rep[n] = ts + 130; return true; } return false; };
      const fired = {}; for (const n of Object.keys(st)) fired[n] = press(n);
      pad.prev = st;
      if (!Object.values(fired).some(Boolean)) return;
      interact();
      if (screen === 'game') {
        kbMode = true;
        if (fired.up) moveCursor(-1, 0); if (fired.down) moveCursor(1, 0); if (fired.left) moveCursor(0, -1); if (fired.right) moveCursor(0, 1);
        if (fired.a) confirmKey(); if (fired.b) sel = -1;
      } else if (fired.a || fired.start) {
        const primary = { start: '.play', win: '.next', lose: '.again' }[screen];
        if (primary) $(primary).click();
      } else if (fired.b && (screen === 'help' || screen === 'settings' || screen === 'records')) show(backTo === 'game' && !g ? 'start' : backTo);
    }

    function updateHint() {
      if (introCardTimer !== null && vis > introCardTimer) hideCard();
      if (screen !== 'game' || !g || busy() || g.won || g.lost || hint || sel >= 0) return;
      if (vis - lastInput > 9000) { const mv = ENG.findMoves(g); if (mv.length) hint = mv[Math.floor(Math.random() * mv.length)]; }
    }

    // ----- Закрытие: всё останавливается и освобождается -----
    function destroy() {
      if (destroyed) return;
      destroyed = true;
      endRun();
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      ac.abort();
      if (audio) audio.close();
      audio = null;
      freeSprites();
      cv.width = 0; cv.height = 0;
      views.clear(); parts.length = 0; pool.length = 0; fxList.length = 0; queue.length = 0; timers.length = 0;
      host.remove();
      if (inst === api) inst = null;
    }
    function front() {
      if (minimized) toggleMin();
      document.body.appendChild(host); // поверх других плавающих окон
      applyGeometry(u, wx, wy); // вернуть окно в видимую область, если окно браузера менялось
      win.focus({ preventScroll: true });
      wake();
    }

    const api = {
      destroy, front, host,
      get screen() { return screen; }, get game() { return g; }, get busy() { return busy(); }, get run() { return run; },
      get geometry() { return { x: wx, y: wy, u, minimized }; },
      get hint() { return hint; },
      startRun, attempt, show, toggleMin, settings, $, reconcile: () => { reconcile(); lastHud = ''; },
      // Для тестов: совпадает ли картинка с движком (каждой фигуре — свой вид на своей клетке)
      viewsMatch() {
        const bad = [];
        g.cells.forEach((c, i) => {
          if (!c.piece) return;
          const v = views.get(c.piece.id), [r, cc] = rc(i);
          if (!v || v.die || v.kind !== c.piece.kind || v.color !== c.piece.color || (v.up || 0) !== (c.piece.up || 0) || Math.abs(v.x - cc) > 0.01 || Math.abs(v.y - r) > 0.01) bad.push(i);
          if (cellObs[i].ice !== c.ice || cellObs[i].lock !== c.lock || cellObs[i].stone !== c.stone) bad.push('obs' + i);
        });
        const live = [...views.values()].filter((v) => !v.die).length, pieces = g.cells.filter((c) => c.piece).length;
        if (live !== pieces) bad.push('count ' + live + '/' + pieces);
        if (disp.score !== g.score || disp.moves !== g.moves) bad.push('hud ' + disp.score + '/' + g.score + ' ' + disp.moves + '/' + g.moves);
        return bad;
      },
      skip(ms) { vis += ms; }, // для тестов: перемотка и стоп-кадр визуального времени
      speed(f) { testSpeed = f; },
      level(n) { if (!run) run = { banked: 0, level: n, seed: 0 }; newLevel(n); },
      onFrame: null,
    };
    buildSprites();
    show('start');
    wake();
    return api;
  }

  function open() { if (inst) { inst.front(); return inst; } inst = createInstance(); return inst; }
  function close() { if (inst) inst.destroy(); }
  return { open, close, test: { engine: ENG, open, close, get inst() { return inst; }, get frames() { return frames; } } };
})();

// Сочетание Alt+M ловит src/crystalsHotkey.ts (код игры подгружается при первом нажатии); здесь — только вход и выход.
export const open = game.open;
export const close = game.close;
export const test = game.test;
