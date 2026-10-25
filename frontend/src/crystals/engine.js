// Встроенная мини-игра «Кристаллики» (служебный модуль): игровая логика без DOM и графики, детерминирована по seed.
// Исходная реализация сохранена без упрощений; изменён только способ подключения (ES-модуль вместо глобальной переменной).
const __root = {};
/* ENGINE START — игровая логика «Кристалликов»: без DOM и графики, детерминирована по seed. */
(function (root) {
  'use strict';

  // ---------- Конфигурация баланса и таймингов ----------
  const CONFIG = {
    size: 7,
    points: { gem: 100, special: 100, ice: 150, lock: 150, stone: 200 },
    groupBonus: { rh: 200, rv: 200, p: 150, b: 400, m: 600 }, // за создание бонуса сверх уничтоженных кристаллов
    comboBonus: { rr: 500, rb: 800, bb: 1200, pp: 400, pr: 500, pb: 700, mn: 300, mr: 1500, mb: 2000, mp: 1500, mm: 3000 },
    upgradeBonus: 500, // превращение: бонус внутри новой фигуры своего вида становится супер-бонусом
    // Лавка между уровнями: цены в осколках; заработок — за звёзды и очки уровня
    shop: { prices: { r: 35, p: 40, b: 60, m: 110, moves: 50 }, max: 3, coins: (stars, score) => 10 + stars * 15 + Math.floor(score / 3000) * 2, extraMoves: 5 },
    cascade: { step: 0.5, max: 4 }, // волна 1: ×1, 2: ×1,5, 3: ×2 … не выше ×4
    finale: { maxRockets: 10, extraMovePoints: 500 },
    safeRefillAfter: 20, // после стольких волн досыпаем без готовых совпадений — каскад не бесконечен
    maxWaves: 40,
    // мс игрового времени; логика их только записывает в события, скорость кадров на неё не влияет
    timing: { rocketCell: 38, bombDelay: 130, bombRing: 22, propLift: 110, propFlight: 520, megaTransform: 380, megaBeam: 460, megaChain: 85, comboDelay: 170, mmDelay: 320, mmStep: 45, create: 140 },
  };

  // ---------- Случайные числа с сохраняемым состоянием (для повтора уровня и клонов в тестах) ----------
  function makeRng(seed) {
    const r = { s: (seed >>> 0) || 1 };
    r.next = function () {
      let t = (r.s = (r.s + 0x6D2B79F5) >>> 0);
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    r.int = (n) => Math.floor(r.next() * n);
    return r;
  }

  // ---------- Уровни ----------
  // Раскладка: . обычная клетка, i лёд, I двойной лёд, l цепь, L цепь на льду, s камень, S крепкий камень.
  // Цели: score — очки, collect {цвет: число}, ice/locks/stones — убрать все такие препятствия.
  // Цвета: 0 красный, 1 синий, 2 зелёный, 3 золотой, 4 фиолетовый, 5 голубой.
  const E = '.......';
  // Подобраны ботом с просмотром на один ход: ему нужно 16–34 хода из 30–45 — около трёх минут живой игры.
  const LEVELS = [
    { moves: 36, colors: 6, goals: { score: 22000 }, stars: [22000, 32000, 42000], tip: 'Меняй местами соседние кристаллы и собирай по три' },
    { moves: 32, colors: 6, goals: { score: 25000 }, stars: [25000, 32000, 38000], tip: 'Четыре в линию — ракета. Сдвинь её, чтобы запустить' },
    { moves: 38, colors: 6, goals: { collect: { 0: 30 } }, stars: [15000, 33000, 38000], tip: 'Квадрат 2×2 — пропеллер: он сам найдёт цель' },
    { moves: 38, colors: 6, goals: { collect: { 1: 24, 3: 24 } }, stars: [16500, 35500, 42000], tip: 'Пять буквой Т или Г — бомба' },
    { moves: 32, colors: 6, goals: { ice: 1, collect: { 2: 34 } }, layout: [E, E, '.iiiii.', '.iiiii.', '.iiiii.', E, E], stars: [13000, 33000, 39000], tip: 'Лёд трескается, когда исчезает кристалл над ним' },
    { moves: 38, colors: 6, goals: { score: 33000 }, stars: [33000, 38000, 41000], tip: 'Пять в линию — мегакристалл. Сдвинь его на любой цвет' },
    { moves: 42, colors: 6, goals: { ice: 1, collect: { 5: 32 } }, layout: [E, '.iiiii.', '.iIIIi.', '.iI.Ii.', '.iIIIi.', '.iiiii.', E], stars: [21000, 49500, 61500], tip: 'Двойной лёд нужно разбить дважды' },
    { moves: 41, colors: 6, goals: { locks: 1, collect: { 4: 31 } }, layout: [E, '.l.l.l.', E, 'l..l..l', E, '.l.l.l.', E], stars: [11500, 32500, 39000], tip: 'Цепь спадёт, если кристалл попадёт в комбинацию' },
    { moves: 41, colors: 6, goals: { stones: 1, collect: { 0: 34 } }, layout: [E, E, E, 'SSS.SSS', E, E, E], stars: [16000, 34500, 40000], tip: 'Камни трескаются от соседних совпадений и бонусов' },
    { moves: 46, colors: 6, goals: { ice: 1, collect: { 4: 33 } }, layout: [E, E, E, E, E, 'IIIIIII', 'IIIIIII'], stars: [20500, 48000, 55000], tip: 'Поменяй местами два бонуса — сработают вместе и сильнее' },
    { moves: 37, colors: 6, goals: { locks: 1, ice: 1, collect: { 1: 36 } }, layout: [E, '.L...L.', '..iii..', '.liiil.', '..iii..', '.L...L.', E], stars: [16500, 36500, 41500], tip: 'Собери новую ракету вместе со старой — получится супер-ракета' },
    { moves: 40, colors: 6, goals: { stones: 1, score: 37500 }, layout: [E, '...S...', '..S.S..', '.S...S.', '..S.S..', '...S...', E], stars: [37500, 44000, 49000], tip: 'Превращение работает с любым бонусом: бомба в бомбе — супер-бомба' },
    { moves: 34, colors: 6, goals: { collect: { 0: 33, 2: 33, 5: 33 } }, stars: [15500, 35500, 49500] },
    { moves: 38, colors: 6, goals: { ice: 1, collect: { 3: 5 } }, layout: [E, E, E, E, 'IIIIIII', 'IIIIIII', 'IIIIIII'], stars: [17500, 38500, 67000] },
    { moves: 36, colors: 6, goals: { score: 29000 }, stars: [29000, 39000, 44000] },
    { moves: 43, colors: 6, goals: { locks: 1, stones: 1, collect: { 2: 45 } }, layout: [E, '.S.l.S.', E, 'l.S.S.l', E, '.S.l.S.', E], stars: [20500, 46000, 53500] },
    { moves: 42, colors: 6, goals: { ice: 1, locks: 1, collect: { 0: 5 } }, layout: ['IiiiiiI', 'i.....i', 'i.l.l.i', 'i.....i', 'i.l.l.i', 'i.....i', 'IiiiiiI'], stars: [17500, 40000, 46500] },
    { moves: 34, colors: 6, goals: { score: 31500, ice: 1, locks: 1 }, layout: [E, '.i.i.i.', 'l.....l', '.i.L.i.', 'l.....l', '.i.i.i.', E], stars: [31500, 36500, 38500] },
    { moves: 45, colors: 6, goals: { stones: 1, collect: { 1: 40, 3: 40 } }, layout: [E, E, 'SS...SS', E, '..sss..', E, E], stars: [21000, 50000, 54500] },
    { moves: 37, colors: 6, goals: { ice: 1, locks: 1, stones: 1, collect: { 5: 36 } }, layout: [E, '.IsIsI.', '.l...l.', '.I.S.I.', '.l...l.', '.IsIsI.', E], stars: [17500, 37500, 46000], tip: 'Последний маяк. Дальше — бесконечные пещеры' },
  ];

  // После 20-го — процедурные уровни: сложность растёт медленно и упирается в потолок.
  function proceduralLevel(n) {
    const rng = makeRng(n * 7919 + 13);
    const d = Math.min(1, (n - 20) / 30); // 0…1
    const rows = [];
    for (let r = 0; r < 7; r++) rows.push(['.', '.', '.', '.', '.', '.', '.']);
    const kind = n % 5;
    const goals = {};
    const put = (ch, count) => {
      let placed = 0, guard = 0;
      while (placed < count && guard++ < 200) {
        const r = 1 + rng.int(6), c = rng.int(4); // зеркально — раскладка выглядит задуманной
        if (rows[r][c] !== '.' || rows[r][6 - c] !== '.') continue;
        rows[r][c] = ch; rows[r][6 - c] = ch; placed += c === 3 ? 1 : 2;
      }
    };
    let moves = 34;
    if (kind === 0) { goals.score = Math.round((30000 + d * 12000) / 500) * 500; moves = 36; }
    if (kind === 1 || kind === 4) { put(rng.next() < 0.4 + d * 0.5 ? 'I' : 'i', 10 + Math.round(d * 8)); goals.ice = 1; goals.collect = { [rng.int(6)]: 24 + Math.round(d * 10) }; moves += 4; }
    if (kind === 2 || kind === 4) { put('l', 5 + Math.round(d * 3)); goals.locks = 1; }
    if (kind === 3) { put('S', 6 + Math.round(d * 4)); goals.stones = 1; put('i', 4); goals.ice = 1; goals.collect = { [rng.int(6)]: 20 + Math.round(d * 10) }; moves += 6; }
    if (kind === 2) { goals.collect = { [rng.int(6)]: 34 + Math.round(d * 10) }; }
    const r5 = (x) => Math.round(x / 500) * 500;
    const stars = goals.score ? [goals.score, r5(goals.score * 1.3), r5(goals.score * 1.6)] : [r5(moves * 450), r5(moves * 1000), r5(moves * 1250)];
    return { moves, colors: 6, goals, layout: rows.map((r) => r.join('')), stars };
  }

  function getLevel(n) { return n <= LEVELS.length ? LEVELS[n - 1] : proceduralLevel(n); }

  // ---------- Состояние ----------
  const isSpecial = (p) => p.kind !== 'n';
  const snap = (p) => ({ id: p.id, color: p.color, kind: p.kind, up: p.up || 0 });
  const cls = (p) => (p.kind === 'rh' || p.kind === 'rv' ? 'r' : p.kind);
  function newPiece(g, color, kind, up) { return { id: g.nextId++, color: kind === 'm' ? -1 : color, kind: kind || 'n', up: up ? 1 : 0 }; }

  // boosters: { r, p, b, m, moves } — купленные в лавке; ставятся в случайные клетки вместо кристаллов
  function createGame(levelNum, seed, boosters) {
    const L = getLevel(levelNum), N = CONFIG.size;
    const cells = [];
    for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
      const ch = (L.layout && L.layout[r] && L.layout[r][c]) || '.';
      cells.push({ piece: null, ice: ch === 'i' || ch === 'L' ? 1 : ch === 'I' ? 2 : 0, lock: ch === 'l' || ch === 'L' ? 1 : 0, stone: ch === 's' ? 1 : ch === 'S' ? 2 : 0 });
    }
    const g = { N, level: L, levelNum, seed, rng: makeRng(seed), nextId: 1, cells, moves: L.moves, score: 0, collected: [0, 0, 0, 0, 0, 0], won: false, lost: false, safeRefill: false };
    g.initial = { ice: count(g, (c) => c.ice > 0), locks: count(g, (c) => c.lock > 0), stones: count(g, (c) => c.stone > 0) };
    generate(g);
    if (boosters) placeBoosters(g, boosters);
    return g;
  }
  function placeBoosters(g, B) {
    const cand = [];
    g.cells.forEach((c, i) => { if (c.piece && c.piece.kind === 'n' && !c.lock) cand.push(i); });
    for (let k = cand.length - 1; k > 0; k--) { const j = g.rng.int(k + 1); [cand[k], cand[j]] = [cand[j], cand[k]]; }
    let n = 0;
    for (const kind of ['m', 'b', 'p', 'r']) for (let q = 0; q < (B[kind] || 0) && n < cand.length; q++) {
      const p = g.cells[cand[n++]].piece; // цвет остаётся прежним — готовых совпадений не появится
      p.kind = kind === 'r' ? (g.rng.next() < 0.5 ? 'rh' : 'rv') : kind;
      if (kind === 'm') p.color = -1;
    }
    if (B.moves) g.moves += B.moves * CONFIG.shop.extraMoves;
  }
  const count = (g, f) => g.cells.reduce((n, c) => n + (f(c) ? 1 : 0), 0);

  function cloneGame(g) {
    const c = Object.assign({}, g);
    c.rng = makeRng(1); c.rng.s = g.rng.s;
    c.cells = g.cells.map((x) => ({ piece: x.piece && { ...x.piece }, ice: x.ice, lock: x.lock, stone: x.stone }));
    c.collected = g.collected.slice();
    return c;
  }

  function goalState(g) {
    const G = g.level.goals, out = [];
    if (G.score) out.push({ type: 'score', target: G.score, left: Math.max(0, G.score - g.score) });
    if (G.collect) for (const k of Object.keys(G.collect)) out.push({ type: 'collect', color: +k, target: G.collect[k], left: Math.max(0, G.collect[k] - g.collected[k]) });
    if (G.ice) out.push({ type: 'ice', target: g.initial.ice, left: count(g, (c) => c.ice > 0) });
    if (G.locks) out.push({ type: 'locks', target: g.initial.locks, left: count(g, (c) => c.lock > 0) });
    if (G.stones) out.push({ type: 'stones', target: g.initial.stones, left: count(g, (c) => c.stone > 0) });
    return out;
  }
  const goalsMet = (g) => goalState(g).every((x) => x.left === 0);
  function starsFor(g) { const s = g.level.stars; return g.won ? 1 + (g.score >= s[1] ? 1 : 0) + (g.score >= s[2] ? 1 : 0) : 0; }

  // ---------- Поиск совпадений ----------
  function mcol(g, i) { const p = g.cells[i].piece; return p && p.kind !== 'm' ? p.color : -1; }

  // Совпадёт ли кристалл цвета col, если поставить его в клетку i (сама клетка не читается).
  function matchesAt(g, i, col) {
    const N = g.N, r = (i / N) | 0, c = i % N, same = (j) => mcol(g, j) === col;
    let n = 1;
    for (let k = c - 1; k >= 0 && same(r * N + k); k--) n++;
    for (let k = c + 1; k < N && same(r * N + k); k++) n++;
    if (n >= 3) return true;
    n = 1;
    for (let k = r - 1; k >= 0 && same(k * N + c); k--) n++;
    for (let k = r + 1; k < N && same(k * N + c); k++) n++;
    if (n >= 3) return true;
    for (const r0 of [r - 1, r]) for (const c0 of [c - 1, c]) {
      if (r0 < 0 || c0 < 0 || r0 + 1 >= N || c0 + 1 >= N) continue;
      const sq = [r0 * N + c0, r0 * N + c0 + 1, (r0 + 1) * N + c0, (r0 + 1) * N + c0 + 1];
      if (sq.every((j) => j === i || same(j))) return true;
    }
    return false;
  }

  // Все совпадения поля, сгруппированные в связные группы (общая клетка → одна группа).
  function findGroups(g) {
    const N = g.N, runs = [], squares = [];
    for (let r = 0; r < N; r++) for (let c = 0; c < N;) {
      const col = mcol(g, r * N + c); let e = c + 1;
      if (col >= 0) while (e < N && mcol(g, r * N + e) === col) e++;
      if (col >= 0 && e - c >= 3) { const cells = []; for (let k = c; k < e; k++) cells.push(r * N + k); runs.push({ dir: 'h', cells }); }
      c = e;
    }
    for (let c = 0; c < N; c++) for (let r = 0; r < N;) {
      const col = mcol(g, r * N + c); let e = r + 1;
      if (col >= 0) while (e < N && mcol(g, e * N + c) === col) e++;
      if (col >= 0 && e - r >= 3) { const cells = []; for (let k = r; k < e; k++) cells.push(k * N + c); runs.push({ dir: 'v', cells }); }
      r = e;
    }
    for (let r = 0; r < N - 1; r++) for (let c = 0; c < N - 1; c++) {
      const i = r * N + c, col = mcol(g, i);
      if (col >= 0 && mcol(g, i + 1) === col && mcol(g, i + N) === col && mcol(g, i + N + 1) === col) squares.push([i, i + 1, i + N, i + N + 1]);
    }
    if (!runs.length && !squares.length) return [];
    const parent = new Int16Array(N * N).map((_, k) => k);
    const find = (x) => { while (parent[x] !== x) x = parent[x] = parent[parent[x]]; return x; };
    const shapes = runs.concat(squares.map((cells) => ({ dir: 'sq', cells })));
    for (const s of shapes) for (const i of s.cells) parent[find(i)] = find(s.cells[0]);
    const byRoot = new Map();
    for (const s of shapes) {
      const k = find(s.cells[0]);
      if (!byRoot.has(k)) byRoot.set(k, { cells: new Set(), runs: [], squares: [], color: mcol(g, s.cells[0]) });
      const grp = byRoot.get(k);
      for (const i of s.cells) grp.cells.add(i);
      (s.dir === 'sq' ? grp.squares : grp.runs).push(s);
    }
    return [...byRoot.values()];
  }

  // Приоритет: 5+ в линию → мегакристалл, Т/Г → бомба, 4 в линию → ракета, квадрат → пропеллер.
  function determineSpecial(g, grp, pref) {
    const N = g.N;
    let kind = null, def = null;
    const r5 = grp.runs.find((r) => r.cells.length >= 5);
    if (r5) { kind = 'm'; def = r5.cells; }
    if (!kind) {
      outer: for (const h of grp.runs) if (h.dir === 'h') for (const v of grp.runs) if (v.dir === 'v') {
        const x = h.cells.find((i) => v.cells.includes(i));
        if (x !== undefined) { kind = 'b'; def = [x]; break outer; }
      }
    }
    if (!kind) { const r4 = grp.runs.find((r) => r.cells.length === 4); if (r4) { kind = r4.dir === 'h' ? 'rh' : 'rv'; def = r4.cells; } }
    if (!kind && grp.squares.length) { kind = 'p'; def = grp.squares[0].cells; }
    if (!kind) return null;
    const ok = (i) => !g.cells[i].lock;
    let at = -1;
    for (const p of pref) if (grp.cells.has(p) && ok(p)) { at = p; break; }
    // Каскад: перекрестье Т/Г, иначе нижняя, затем левая клетка фигуры.
    const order = (a, b) => ((b / N) | 0) - ((a / N) | 0) || (a % N) - (b % N);
    if (at < 0) at = def.filter(ok).sort(order)[0] ?? -1;
    if (at < 0) at = [...grp.cells].filter(ok).sort(order)[0] ?? -1;
    if (at < 0) return null;
    return { kind, at, color: grp.color };
  }

  function findMoves(g) {
    const N = g.N, out = [], cells = g.cells;
    const movable = (i) => cells[i].piece && !cells[i].lock;
    for (let i = 0; i < N * N; i++) {
      const r = (i / N) | 0, c = i % N;
      for (const j of [c + 1 < N ? i + 1 : -1, r + 1 < N ? i + N : -1]) {
        if (j < 0 || !movable(i) || !movable(j)) continue;
        const pi = cells[i].piece, pj = cells[j].piece;
        if (isSpecial(pi) || isSpecial(pj)) { out.push([i, j]); continue; }
        if (pi.color === pj.color) continue;
        cells[i].piece = pj; cells[j].piece = pi;
        const ok = matchesAt(g, i, pj.color) || matchesAt(g, j, pi.color);
        cells[i].piece = pi; cells[j].piece = pj;
        if (ok) out.push([i, j]);
      }
    }
    return out;
  }

  // ---------- Генерация и досыпание ----------
  function safeColor(g, i) {
    const n = g.level.colors, start = g.rng.int(n);
    for (let k = 0; k < n; k++) { const col = (start + k) % n; if (!matchesAt(g, i, col)) return col; }
    return start;
  }

  function generate(g) {
    for (let attempt = 0; attempt < 300; attempt++) {
      for (const c of g.cells) c.piece = null;
      g.cells.forEach((c, i) => { if (!c.stone) c.piece = newPiece(g, safeColor(g, i), 'n'); });
      if (!findGroups(g).length && findMoves(g).length >= 3) return;
    }
    shuffleBoard(g);
  }

  function refillColor(g, i) { return g.safeRefill ? safeColor(g, i) : g.rng.int(g.level.colors); }

  // Падение внутри столбца; камни и закованные кристаллы стоят на месте и делят столбец на участки.
  function gravity(g) {
    const N = g.N, moves = [], spawns = [];
    const blocker = (i) => g.cells[i].stone > 0 || (g.cells[i].piece && g.cells[i].lock > 0);
    for (let c = 0; c < N; c++) {
      let r = N - 1;
      while (r >= 0) {
        if (blocker(r * N + c)) { r--; continue; }
        const bottom = r;
        while (r >= 0 && !blocker(r * N + c)) r--;
        const top = r + 1;
        let write = bottom;
        for (let rr = bottom; rr >= top; rr--) {
          const p = g.cells[rr * N + c].piece;
          if (!p) continue;
          if (rr !== write) { g.cells[write * N + c].piece = p; g.cells[rr * N + c].piece = null; moves.push({ id: p.id, from: rr * N + c, to: write * N + c }); }
          write--;
        }
        for (let rr = write, k = 0; rr >= top; rr--, k++) {
          const i = rr * N + c, p = newPiece(g, refillColor(g, i), 'n');
          g.cells[i].piece = p;
          spawns.push({ ...snap(p), to: i, fromRow: top - 1 - k });
        }
      }
    }
    return { type: 'fall', moves, spawns };
  }

  function shuffleBoard(g) {
    const idx = [];
    g.cells.forEach((c, i) => { if (c.piece && c.piece.kind === 'n' && !c.lock) idx.push(i); });
    const pieces = idx.map((i) => g.cells[i].piece);
    const done = (recolor) => ({ type: 'shuffle', moves: idx.map((i) => ({ id: g.cells[i].piece.id, to: i })), recolor });
    for (let attempt = 0; attempt < 120; attempt++) {
      for (let k = pieces.length - 1; k > 0; k--) { const j = g.rng.int(k + 1); [pieces[k], pieces[j]] = [pieces[j], pieces[k]]; }
      idx.forEach((i, k) => { g.cells[i].piece = pieces[k]; });
      if (!findGroups(g).length && findMoves(g).length) return done(null);
    }
    for (let attempt = 0; attempt < 120; attempt++) {
      for (const i of idx) g.cells[i].piece = null;
      for (let k = 0; k < idx.length; k++) { pieces[k].color = safeColor(g, idx[k]); g.cells[idx[k]].piece = pieces[k]; }
      if (!findGroups(g).length && findMoves(g).length) return done(pieces.map(snap));
    }
    // Последний рубеж: пропеллер можно сдвинуть с любым соседом — ход гарантирован.
    if (pieces.length) pieces[0].kind = 'p';
    return done(pieces.map(snap));
  }

  // ---------- Волна: совпадения, удары и цепные реакции в одном событийном цикле ----------
  // Каждая клетка за волну получает не больше одного удара — двойного начисления не бывает.
  function heapPush(w, t, fn) {
    const h = w.heap, e = { t, s: w.seq++, fn };
    h.push(e);
    for (let k = h.length - 1; k > 0;) {
      const p = (k - 1) >> 1;
      if (h[p].t < e.t || (h[p].t === e.t && h[p].s < e.s)) break;
      h[k] = h[p]; h[p] = e; k = p;
    }
  }
  function heapPop(w) {
    const h = w.heap, top = h[0], last = h.pop();
    if (h.length) {
      h[0] = last;
      for (let k = 0; ;) {
        const a = 2 * k + 1, b = a + 1;
        let m = k;
        const less = (x, y) => h[x].t < h[y].t || (h[x].t === h[y].t && h[x].s < h[y].s);
        if (a < h.length && less(a, m)) m = a;
        if (b < h.length && less(b, m)) m = b;
        if (m === k) break;
        [h[k], h[m]] = [h[m], h[k]]; k = m;
      }
    }
    return top;
  }

  function newSrc(w, i) { const N = w.g.N; const s = { r: (i / N) | 0, c: i % N, pts: 0, t: 0 }; w.sources.push(s); return s; }
  function addPts(w, src, pts, t) { const v = Math.round(pts * w.mult); w.points += v; src.pts += v; if (t > src.t) src.t = t; }

  function hit(w, i, t, src, toward, noActivate) {
    if (w.hit[i]) return;
    w.hit[i] = 1;
    const g = w.g, cell = g.cells[i], P = CONFIG.points;
    if (cell.stone) { cell.stone--; addPts(w, src, P.stone, t); w.events.push({ t, type: 'obs', i, what: 'stone', left: cell.stone }); return; }
    const p = cell.piece;
    if (!p) return;
    if (cell.lock) { cell.lock = 0; addPts(w, src, P.lock, t); w.events.push({ t, type: 'obs', i, what: 'lock', left: 0 }); return; }
    cell.piece = null;
    w.destroyed++;
    if (p.color >= 0) g.collected[p.color]++;
    addPts(w, src, isSpecial(p) ? P.special : P.gem, t);
    w.events.push({ t, type: 'destroy', i, id: p.id, color: p.color, kind: p.kind, toward: toward ?? -1 });
    if (cell.ice) { cell.ice--; addPts(w, src, P.ice, t); w.events.push({ t, type: 'obs', i, what: 'ice', left: cell.ice }); }
    if (isSpecial(p) && !noActivate) activate(w, p, i, t);
  }
  const hitAt = (w, i, t, src) => heapPush(w, t, () => hit(w, i, t, src));

  function activate(w, p, i, t) {
    w.activations++;
    if (p.kind === 'rh' || p.kind === 'rv') { if (p.up) wideRocket(w, i, t, p.kind === 'rh', 1, p.color); else fireRocket(w, i, t, p.kind === 'rh', p.kind === 'rv', p.color); }
    else if (p.kind === 'b') explode(w, i, t, p.up ? 3 : 2, p.color);
    else if (p.kind === 'p') propeller(w, i, t, p.up ? 'b1' : null, p.color, true, 0);
    else if (p.kind === 'm') mega(w, i, t, null, null);
  }
  // Супер-ракета и усиленные сочетания: несколько параллельных линий (half — сколько линий по бокам)
  function wideRocket(w, i, t, horiz, half, color) {
    const N = w.g.N, r = (i / N) | 0, c = i % N;
    for (let d = -half; d <= half; d++) {
      if (horiz && r + d >= 0 && r + d < N) fireRocket(w, (r + d) * N + c, t, true, false, color);
      if (!horiz && c + d >= 0 && c + d < N) fireRocket(w, r * N + c + d, t, false, true, color);
    }
  }
  // Диагонали «звезды» (ракета + ракета)
  function fireDiag(w, i, t, color) {
    const N = w.g.N, r = (i / N) | 0, c = i % N, src = newSrc(w, i), T = CONFIG.timing;
    w.events.push({ t, type: 'rocket', i, diag: true, color });
    for (let d = 1; d < N; d++) for (const [dr, dc] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const rr = r + dr * d, cc = c + dc * d;
      if (rr >= 0 && rr < N && cc >= 0 && cc < N) hitAt(w, rr * N + cc, t + d * T.rocketCell * 1.3, src);
    }
  }

  function fireRocket(w, i, t, horiz, vert, color) {
    const N = w.g.N, r = (i / N) | 0, c = i % N, src = newSrc(w, i), T = CONFIG.timing;
    w.events.push({ t, type: 'rocket', i, h: horiz, v: vert, color });
    hitAt(w, i, t, src);
    for (let d = 1; d < N; d++) {
      const td = t + d * T.rocketCell;
      if (horiz) { if (c - d >= 0) hitAt(w, i - d, td, src); if (c + d < N) hitAt(w, i + d, td, src); }
      if (vert) { if (r - d >= 0) hitAt(w, i - d * N, td, src); if (r + d < N) hitAt(w, i + d * N, td, src); }
    }
  }

  function explode(w, i, t, rad, color) {
    const N = w.g.N, r = (i / N) | 0, c = i % N, src = newSrc(w, i), T = CONFIG.timing;
    w.events.push({ t, type: 'bomb', i, rad, color });
    hitAt(w, i, t, src);
    for (let dr = -rad; dr <= rad; dr++) for (let dc = -rad; dc <= rad; dc++) {
      const rr = r + dr, cc = c + dc;
      if (rr >= 0 && rr < N && cc >= 0 && cc < N) hitAt(w, rr * N + cc, t + T.bombDelay + Math.max(Math.abs(dr), Math.abs(dc)) * T.bombRing, src);
    }
  }

  function area(w, i, carry) {
    const N = w.g.N, r = (i / N) | 0, c = i % N, out = [];
    if (!carry) return [i];
    if (carry === 'rh' || carry === 'Rh') for (let k = 0; k < N; k++) out.push(r * N + k);
    else if (carry === 'rv' || carry === 'Rv') for (let k = 0; k < N; k++) out.push(k * N + c);
    else { const R = carry === 'b1' ? 1 : carry === 'b3' ? 3 : 2; for (let dr = -R; dr <= R; dr++) for (let dc = -R; dc <= R; dc++) { const rr = r + dr, cc = c + dc; if (rr >= 0 && rr < N && cc >= 0 && cc < N) out.push(rr * N + cc); } }
    return out;
  }

  // Ценность клетки как цели пропеллера: сначала то, что нужно для цели уровня.
  function cellValue(w, j) {
    const g = w.g, cell = g.cells[j], G = g.level.goals;
    if (cell.stone) return G.stones ? 100 : 25;
    const p = cell.piece;
    if (!p) return 0;
    let v = 8;
    if (cell.lock) v += G.locks ? 90 : 20;
    if (cell.ice) v += G.ice ? 60 + 15 * cell.ice : 15;
    if (G.collect && p.color >= 0 && G.collect[p.color] && g.collected[p.color] < G.collect[p.color]) v += 45;
    if (isSpecial(p)) v += 12;
    return v;
  }

  function chooseTarget(w, carry) {
    const g = w.g; let best = -1, bestI = -1;
    for (let i = 0; i < g.N * g.N; i++) {
      if (w.hit[i] || w.claimed.has(i)) continue;
      const cell = g.cells[i];
      if (!cell.stone && !cell.piece) continue;
      let v = 0;
      for (const j of area(w, i, carry)) if (!w.hit[j]) v += cellValue(w, j);
      v += g.rng.next() * 0.5;
      if (v > best) { best = v; bestI = i; }
    }
    return bestI;
  }

  function propeller(w, i, t, carry, color, startHits, delay) {
    const N = w.g.N, T = CONFIG.timing, src = newSrc(w, i);
    if (startHits) {
      w.events.push({ t, type: 'propUp', i, color });
      hitAt(w, i, t, src);
      const r = (i / N) | 0, c = i % N;
      if (r > 0) hitAt(w, i - N, t + T.propLift * 0.6, src);
      if (r < N - 1) hitAt(w, i + N, t + T.propLift * 0.6, src);
      if (c > 0) hitAt(w, i - 1, t + T.propLift * 0.6, src);
      if (c < N - 1) hitAt(w, i + 1, t + T.propLift * 0.6, src);
    }
    const launch = t + T.propLift + delay;
    heapPush(w, launch, () => {
      const tgt = chooseTarget(w, carry);
      if (tgt < 0) return;
      w.claimed.add(tgt);
      const arrive = launch + T.propFlight;
      w.events.push({ t: launch, type: 'propFly', from: i, to: tgt, dur: T.propFlight, carry, color });
      heapPush(w, arrive, () => {
        w.claimed.delete(tgt);
        if (carry === 'rh' || carry === 'rv') fireRocket(w, tgt, arrive, carry === 'rh', carry === 'rv', color);
        else if (carry === 'Rh' || carry === 'Rv') wideRocket(w, tgt, arrive, carry === 'Rh', 1, color);
        else if (carry === 'b' || carry === 'b1' || carry === 'b3') explode(w, tgt, arrive, carry === 'b1' ? 1 : carry === 'b3' ? 3 : 2, color);
        else hit(w, tgt, arrive, newSrc(w, tgt));
      });
    });
  }

  function mostCommonColor(w) {
    const n = [0, 0, 0, 0, 0, 0];
    w.g.cells.forEach((c, i) => { if (!w.hit[i] && c.piece && c.piece.kind === 'n') n[c.piece.color]++; });
    const m = Math.max(...n);
    return m > 0 ? n.indexOf(m) : -1;
  }

  // Мегакристалл: лучи ко всем обычным кристаллам цвета; с бонусом — сначала превращает их в бонусы.
  function mega(w, i, t, color, transform, up) {
    const g = w.g, N = g.N, T = CONFIG.timing, src = newSrc(w, i);
    if (color == null) color = mostCommonColor(w);
    hitAt(w, i, t, src);
    if (color < 0) return;
    const r0 = (i / N) | 0, c0 = i % N;
    const targets = [];
    g.cells.forEach((c, j) => { if (j !== i && !w.hit[j] && c.piece && c.piece.kind === 'n' && c.piece.color === color) targets.push(j); });
    targets.sort((a, b) => (Math.abs(((a / N) | 0) - r0) + Math.abs(a % N - c0)) - (Math.abs(((b / N) | 0) - r0) + Math.abs(b % N - c0)));
    w.events.push({ t, type: 'beam', i, targets, color, transform: transform || null });
    if (!transform) { for (const j of targets) hitAt(w, j, t + T.megaBeam, src); return; }
    heapPush(w, t + T.megaTransform, () => {
      targets.forEach((j, k) => {
        const p = g.cells[j].piece;
        if (w.hit[j] || !p || p.kind !== 'n') return;
        p.kind = transform === 'r' ? (k % 2 ? 'rv' : 'rh') : transform;
        if (up) p.up = 1;
        w.events.push({ t: t + T.megaTransform, type: 'transform', i: j, id: p.id, kind: p.kind, up: p.up || 0 });
      });
    });
    targets.forEach((j, k) => hitAt(w, j, t + T.megaBeam + k * T.megaChain, src));
  }

  function comboKey(a, b) {
    const order = 'mbpr'; // мегакристалл главнее бомбы, бомба — пропеллера, пропеллер — ракеты
    const x = cls(a), y = cls(b);
    const [hi, lo] = order.indexOf(x) <= order.indexOf(y) ? [x, y] : [y, x];
    const key = { mm: 'mm', mb: 'mb', mp: 'mp', mr: 'mr', bb: 'bb', bp: 'pb', br: 'rb', pp: 'pp', pr: 'pr', rr: 'rr' }[hi + lo];
    return { key, hiPiece: cls(a) === hi ? a : b, loPiece: cls(a) === hi ? b : a };
  }

  function runCombo(w, key, hiP, loP, i, t) {
    const src = newSrc(w, i);
    addPts(w, src, CONFIG.comboBonus[key], t);
    w.events.push({ t: 0, type: 'combo', i, key, color: loP.color });
    w.combos.push(key);
    const N = w.g.N;
    const boost = (hiP.up || 0) + (loP.up || 0); // каждый супер-бонус в сочетании добавляет мощи
    if (boost) addPts(w, src, CONFIG.upgradeBonus * boost, t);
    switch (key) {
      case 'rr': // «звезда»: строка, столбец и обе диагонали; с супер-ракетой — ещё по линии с каждой стороны
        if (boost) { wideRocket(w, i, t, true, 1, hiP.color); wideRocket(w, i, t, false, 1, loP.color); } else fireRocket(w, i, t, true, true, hiP.color);
        fireDiag(w, i, t, loP.color);
        break;
      case 'rb': wideRocket(w, i, t, true, 1 + boost, loP.color); wideRocket(w, i, t, false, 1 + boost, loP.color); break;
      case 'bb': explode(w, i, t, 3 + boost, hiP.color); break;
      case 'pp': for (let q = 0; q < 3 + boost * 2; q++) propeller(w, i, t, boost ? 'b1' : null, q % 2 ? loP.color : hiP.color, q === 0, q * 70); break;
      case 'pr': propeller(w, i, t, boost ? (loP.kind === 'rh' ? 'Rh' : 'Rv') : loP.kind, hiP.color, true, 0); break;
      case 'pb': propeller(w, i, t, boost ? 'b3' : 'b', loP.color, true, 0); break;
      case 'mr': mega(w, i, t, loP.color, 'r', boost); break;
      case 'mb': mega(w, i, t, loP.color, 'b', boost); break;
      case 'mp': mega(w, i, t, loP.color, 'p', boost); break;
      case 'mm': {
        w.events.push({ t, type: 'megamega', i });
        const T = CONFIG.timing;
        const r = (i / N) | 0, c = i % N;
        for (let j = 0; j < N * N; j++) hitAt(w, j, t + T.mmDelay + (Math.abs(((j / N) | 0) - r) + Math.abs(j % N - c)) * T.mmStep, src);
        break;
      }
    }
  }

  function runWave(g, waveIdx, init) {
    const N = g.N, T = CONFIG.timing;
    const w = { g, events: [], hit: new Uint8Array(N * N), heap: [], seq: 0, claimed: new Set(), sources: [], created: [], combos: [],
      mult: Math.min(CONFIG.cascade.max, 1 + CONFIG.cascade.step * waveIdx), points: 0, destroyed: 0, activations: 0 };
    const groups = findGroups(g);
    const pref = (init && init.pref) || [];
    for (const grp of groups) {
      const cells = [...grp.cells];
      const sp = determineSpecial(g, grp, pref);
      const cr = cells.reduce((a, i) => a + ((i / N) | 0), 0) / cells.length, cc = cells.reduce((a, i) => a + (i % N), 0) / cells.length;
      const src = { r: cr, c: cc, pts: 0, t: 0 }; w.sources.push(src);
      // Превращение: бонус того же вида внутри новой фигуры не взрывается, а вливается в новый — получается супер-бонус
      const absorb = sp ? cells.filter((i) => { const p = g.cells[i].piece; return p && p.kind !== 'n' && cls(p) === cls({ kind: sp.kind }) && !p.up; }) : [];
      if (sp) {
        w.created.push({ at: sp.at, piece: newPiece(g, sp.color, sp.kind, absorb.length > 0) });
        addPts(w, src, CONFIG.groupBonus[sp.kind] + (absorb.length ? CONFIG.upgradeBonus : 0), 0);
      }
      w.events.push({ t: 0, type: 'match', cells, color: grp.color, special: sp ? sp.kind : null, at: sp ? sp.at : -1, size: cells.length, upgrade: absorb.length > 0 });
      for (const i of cells) heapPush(w, 0, () => hit(w, i, 0, src, sp ? sp.at : -1, absorb.includes(i)));
      for (const i of cells) for (const j of neighbors(N, i)) if (g.cells[j].stone) heapPush(w, 0, () => hit(w, j, 0, src));
    }
    if (init && init.run) init.run(w);
    if (!w.heap.length) return null;
    while (w.heap.length) heapPop(w).fn();
    for (const cr of w.created) { g.cells[cr.at].piece = cr.piece; w.events.push({ t: T.create, type: 'create', i: cr.at, piece: snap(cr.piece) }); }
    for (const s of w.sources) if (s.pts > 0) w.events.push({ t: s.t, type: 'score', r: s.r, c: s.c, pts: s.pts });
    w.events.sort((a, b) => a.t - b.t);
    g.score += w.points;
    return { type: 'wave', index: waveIdx, mult: w.mult, events: w.events, points: w.points, destroyed: w.destroyed,
      created: w.created.map((x) => x.piece.kind), upgrades: w.created.filter((x) => x.piece.up).length, combos: w.combos, activations: w.activations,
      duration: (w.events.length ? w.events[w.events.length - 1].t : 0) };
  }

  function neighbors(N, i) {
    const r = (i / N) | 0, c = i % N, out = [];
    if (r > 0) out.push(i - N); if (r < N - 1) out.push(i + N); if (c > 0) out.push(i - 1); if (c < N - 1) out.push(i + 1);
    return out;
  }

  function resolve(g, init, steps, noShuffle) {
    let waveIdx = 0;
    for (; waveIdx < CONFIG.maxWaves; waveIdx++) {
      g.safeRefill = waveIdx >= CONFIG.safeRefillAfter;
      const w = runWave(g, waveIdx, waveIdx === 0 ? init : null);
      if (!w) break;
      steps.push(w);
      steps.push(gravity(g));
    }
    g.safeRefill = false;
    if (!noShuffle && findMoves(g).length === 0) steps.push(shuffleBoard(g));
    return waveIdx;
  }

  function summarize(steps) {
    const s = { waves: 0, destroyed: 0, points: 0, created: [], combos: [], activations: 0, maxMatch: 0, upgrades: 0 };
    for (const st of steps) if (st.type === 'wave') {
      s.waves++; s.upgrades += st.upgrades; s.destroyed += st.destroyed; s.points += st.points; s.activations += st.activations;
      s.created.push(...st.created); s.combos.push(...st.combos);
      for (const e of st.events) if (e.type === 'match') s.maxMatch = Math.max(s.maxMatch, e.size);
    }
    return s;
  }

  // ---------- Ход игрока ----------
  function adjacent(N, a, b) { const ra = (a / N) | 0, rb = (b / N) | 0; return (ra === rb && Math.abs(a - b) === 1) || (Math.abs(ra - rb) === 1 && a % N === b % N); }

  function trySwap(g, a, b) {
    const N = g.N, cells = g.cells;
    if (g.won || g.lost || a === b || a < 0 || b < 0 || a >= N * N || b >= N * N || !adjacent(N, a, b)) return { valid: false };
    const pa = cells[a].piece, pb = cells[b].piece;
    if (!pa || !pb || cells[a].lock || cells[b].lock) return { valid: false, blocked: true };
    const steps = [];
    let init;
    if (isSpecial(pa) && isSpecial(pb)) {
      const { key, hiPiece, loPiece } = comboKey(pa, pb);
      steps.push({ type: 'swap', a, b, ida: pa.id, idb: pb.id, merge: true });
      init = { pref: [], run(w) {
        const src = newSrc(w, b);
        hit(w, a, 0, src, b, true); hit(w, b, 0, src, -1, true);
        heapPush(w, CONFIG.timing.comboDelay, () => runCombo(w, key, hiPiece, loPiece, b, CONFIG.timing.comboDelay));
      } };
    } else {
      cells[a].piece = pb; cells[b].piece = pa;
      if (!isSpecial(pa) && !isSpecial(pb) && !findGroups(g).length) { cells[a].piece = pa; cells[b].piece = pb; return { valid: false, bounce: true, ida: pa.id, idb: pb.id }; }
      steps.push({ type: 'swap', a, b, ida: pa.id, idb: pb.id });
      const sp = isSpecial(pa) ? pa : isSpecial(pb) ? pb : null;
      const spAt = sp === pa ? b : a;
      init = { pref: [b, a], run(w) {
        if (!sp) return;
        if (sp.kind === 'm') {
          const other = sp === pa ? pb : pa;
          const src = newSrc(w, spAt);
          addPts(w, src, CONFIG.comboBonus.mn, 0);
          hit(w, spAt, 0, src, -1, true);
          w.events.push({ t: 0, type: 'combo', i: spAt, key: 'mn', color: other.color });
          mega(w, spAt, 0, other.color, null);
        } else heapPush(w, 0, () => hit(w, spAt, 0, newSrc(w, spAt)));
      } };
    }
    g.moves--;
    resolve(g, init, steps);
    if (goalsMet(g)) g.won = true; else if (g.moves <= 0) g.lost = true;
    return { valid: true, steps, summary: summarize(steps), won: g.won, lost: g.lost };
  }

  // Финал: оставшиеся ходы превращаются в ракеты (не больше maxRockets), остаток — очками.
  function finale(g) {
    const steps = [];
    if (!g.won || g.moves <= 0) return { steps, bonus: 0 };
    const before = g.score, F = CONFIG.finale;
    const cand = [];
    g.cells.forEach((c, i) => { if (c.piece && c.piece.kind === 'n' && !c.lock) cand.push(i); });
    for (let k = cand.length - 1; k > 0; k--) { const j = g.rng.int(k + 1); [cand[k], cand[j]] = [cand[j], cand[k]]; }
    const n = Math.min(g.moves, F.maxRockets, cand.length), extra = g.moves - n;
    const picked = cand.slice(0, n);
    g.moves = 0;
    resolve(g, { pref: [], run(w) {
      picked.forEach((i, k) => {
        heapPush(w, k * 110, () => {
          const p = g.cells[i].piece;
          if (w.hit[i] || !p || p.kind !== 'n') return;
          p.kind = k % 2 ? 'rv' : 'rh';
          w.events.push({ t: k * 110, type: 'transform', i, id: p.id, kind: p.kind, tick: 1 });
        });
        heapPush(w, n * 110 + 250 + k * 90, () => hit(w, i, n * 110 + 250 + k * 90, newSrc(w, i)));
      });
      if (extra > 0) { const src = newSrc(w, 24); addPts(w, src, extra * F.extraMovePoints, 0); w.events.push({ t: 0, type: 'tick', n: extra }); }
    } }, steps, true);
    return { steps, bonus: g.score - before };
  }

  // ---------- Для тестов и бота ----------
  function progress(g) {
    let v = 0;
    for (const x of goalState(g)) v += x.type === 'score' ? (x.target - x.left) / 50 : (x.target - x.left) * (x.type === 'collect' ? 3 : 25);
    return v + g.score / 2000;
  }
  function checkBoard(g) {
    const ids = new Set();
    g.cells.forEach((c, i) => {
      if (c.stone && c.piece) throw new Error('piece on stone ' + i);
      if (!c.stone && !c.piece) throw new Error('empty cell ' + i);
      if (c.piece) { if (ids.has(c.piece.id)) throw new Error('duplicate id ' + c.piece.id); ids.add(c.piece.id); }
    });
    if (findGroups(g).length) throw new Error('board left with matches');
  }

  root.CrystalsEngine = { placeBoosters, shuffleBoard, CONFIG, LEVELS, getLevel, createGame, cloneGame, trySwap, finale, findMoves, findGroups, determineSpecial, goalState, goalsMet, starsFor, progress, checkBoard, makeRng, adjacent, gravity, matchesAt, comboKey };
})(__root);
export const CrystalsEngine = __root.CrystalsEngine;
