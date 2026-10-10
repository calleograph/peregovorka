/**
 * Сцена встречи: ЧТО показывать крупно и ГДЕ стоит каждая плитка. Чистые функции без React и LiveKit — проверяются тестами (stageModel.test.ts).
 *
 * Три независимых состояния: медиа (кто что публикует — приходит из LiveKit), раскладка (личные закрепления и режим зрителя — только в его браузере)
 * и модерация (общая сцена ведущего — на сервере, событие stage_changed). Здесь сходятся первые два и сцена ведущего; права проверяет сервер.
 *
 * Элементы сцены адресуются устойчивыми ключами «тип:участник» (camera:<identity>, screen:<identity>, board), а не SID дорожек: после переподключения
 * у дорожки новый SID, а закрепление и общая сцена остаются верными. Ключ отсутствующего сейчас элемента просто не действует, пока тот не вернётся.
 */

export type StageType = "camera" | "screen" | "board";
export type LayoutMode = "auto" | "grid" | "stage" | "side";
export interface StageItem { key: string; type: StageType; identity?: string; title: string; local?: boolean; since: number }
export interface Rect { x: number; y: number; w: number; h: number }
export interface SpotItem { type: StageType; identity?: string }

export const MAX_PINS = 4;          // больше четырёх крупных элементов не читаются; на телефоне крупно — один
export const GRID_AUTO_MAX = 9;     // «Авто» без показа экрана: сетка, пока плитки крупные; дальше — говорящий крупно и лента
export const GRID_MAX = 16;         // «Сетка»: не больше 16 ячеек, остальные — в ленте
export const MOBILE_W = 600;
export const LAYOUTS: { id: LayoutMode; label: string; hint: string }[] = [
  { id: "auto", label: "Авто", hint: "Показ экрана и доска — крупно, без них — сетка или говорящий" },
  { id: "grid", label: "Сетка", hint: "Все участники одинаковыми плитками" },
  { id: "stage", label: "Сцена", hint: "Главное крупно, остальные — лентой снизу" },
  { id: "side", label: "Рядом", hint: "Главное крупно, остальные — колонкой справа" },
];

export const keyOf = (type: StageType, identity?: string): string => (type === "board" ? "board" : `${type}:${identity ?? ""}`);
export const parseKey = (key: string): SpotItem | null => {
  if (key === "board") return { type: "board" };
  const i = key.indexOf(":");
  const type = key.slice(0, i);
  return i > 0 && (type === "camera" || type === "screen") && key.length > i + 1 ? { type, identity: key.slice(i + 1) } : null;
};
export const isLayout = (v: unknown): v is LayoutMode => v === "auto" || v === "grid" || v === "stage" || v === "side";

export interface Source { identity: string; name: string; local: boolean; screen: boolean }

/** Момент первого появления каждого ключа (новый показ экрана — «свежее» старого). Исчезнувшие ключи забываются: вернувшийся показ снова свежий. */
export function trackSince(prev: ReadonlyMap<string, number>, keys: string[], now: number): Map<string, number> {
  const next = new Map<string, number>();
  for (const k of keys) next.set(k, prev.get(k) ?? now);
  return next;
}

/** Элементы сцены: камера (или аватар) каждого участника, каждый показ экрана отдельно, доска — если открыта у зрителя или показана ведущим. */
export function buildItems(ps: Source[], board: boolean, since: ReadonlyMap<string, number>): StageItem[] {
  const out: StageItem[] = [];
  for (const p of ps) {
    if (p.screen) { const key = keyOf("screen", p.identity); out.push({ key, type: "screen", identity: p.identity, title: `Экран: ${p.name}`, local: p.local, since: since.get(key) ?? 0 }); }
  }
  if (board) out.push({ key: "board", type: "board", title: "Доска", since: since.get("board") ?? 0 });
  for (const p of ps) { const key = keyOf("camera", p.identity); out.push({ key, type: "camera", identity: p.identity, title: p.name, local: p.local, since: since.get(key) ?? 0 }); }
  return out;
}

export type MainReason = "pins" | "spotlight" | "screen" | "board" | "speaker" | "grid";
export interface Choice { main: string[]; reason: MainReason; mode: "grid" | "stage" | "side" }

/**
 * Что крупно. Личное важнее общего: закреплённое зрителем → общая сцена ведущего (если зритель её не перекрыл) → свежий показ экрана чужого участника →
 * доска → говорящий. Свой собственный экран крупно не ставится, пока в комнате есть кто-то ещё (зеркало «экран в экране» никому не нужно).
 */
export function chooseMain(o: { items: StageItem[]; pins: string[]; spotlight: string[]; speaker: string | null; layout: LayoutMode; mobile?: boolean }): Choice {
  const have = new Set(o.items.map((i) => i.key));
  const cams = o.items.filter((i) => i.type === "camera");
  const limit = o.mobile ? 1 : MAX_PINS;
  const side = o.layout === "side" ? "side" : "stage";
  if (o.layout === "grid" && !o.mobile) return { main: [], reason: "grid", mode: "grid" };
  const pins = o.pins.filter((k) => have.has(k)).slice(0, limit);
  if (pins.length) return { main: pins, reason: "pins", mode: side };
  const spot = o.spotlight.filter((k) => have.has(k)).slice(0, limit);
  if (spot.length) return { main: spot, reason: "spotlight", mode: side };
  const screens = o.items.filter((i) => i.type === "screen").sort((a, b) => Number(!!a.local) - Number(!!b.local) || b.since - a.since);
  const top = screens[0];
  if (top && (!top.local || cams.length <= 1)) return { main: [top.key], reason: "screen", mode: side };
  if (have.has("board")) return { main: ["board"], reason: "board", mode: side };
  if (o.layout === "auto" && !o.mobile && cams.length <= GRID_AUTO_MAX) return { main: [], reason: "grid", mode: "grid" };
  const sp = o.speaker && have.has(keyOf("camera", o.speaker)) ? keyOf("camera", o.speaker) : (cams.find((c) => !c.local) ?? cams[0])?.key;
  return sp ? { main: [sp], reason: "speaker", mode: side } : { main: [], reason: "grid", mode: "grid" };
}

/** Порядок ленты: показы экрана и доска — первыми (их ищут глазами), затем участники в порядке комнаты. */
export function stripOrder(items: StageItem[], main: string[]): string[] {
  const m = new Set(main);
  const rank = (i: StageItem) => (i.type === "screen" ? 0 : i.type === "board" ? 1 : 2);
  return items.filter((i) => !m.has(i.key)).sort((a, b) => rank(a) - rank(b)).map((i) => i.key);
}

/** Сетка n ячеек в прямоугольнике: число столбцов подбирается так, чтобы плитка с пропорцией aspect была как можно крупнее. */
export function gridRects(n: number, box: Rect, gap: number, aspect = 16 / 9): Rect[] {
  if (n <= 0 || box.w <= 0 || box.h <= 0) return [];
  let best = { cols: 1, rows: n, tw: 0, th: 0 };
  for (let cols = 1; cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    const cw = (box.w - gap * (cols - 1)) / cols, ch = (box.h - gap * (rows - 1)) / rows;
    if (cw <= 0 || ch <= 0) continue;
    const tw = Math.min(cw, ch * aspect), th = tw / aspect;
    if (tw * th > best.tw * best.th + 0.5) best = { cols, rows, tw, th };
  }
  // ячейки растягиваются на всю доступную площадь (видео вписывается внутри), чтобы не было «дыр» между рядами
  const cw = (box.w - gap * (best.cols - 1)) / best.cols, ch = (box.h - gap * (best.rows - 1)) / best.rows;
  const w = Math.min(cw, Math.max(best.tw, ch * aspect * 1.35)), h = Math.min(ch, Math.max(best.th, cw / aspect * 1.35));
  const out: Rect[] = [];
  const usedH = best.rows * h + gap * (best.rows - 1);
  const y0 = box.y + (box.h - usedH) / 2;
  for (let r = 0; r < best.rows; r++) {
    const inRow = Math.min(best.cols, n - r * best.cols);
    const usedW = inRow * w + gap * (inRow - 1);
    const x0 = box.x + (box.w - usedW) / 2;
    for (let c = 0; c < inRow; c++) out.push(rnd({ x: x0 + c * (w + gap), y: y0 + r * (h + gap), w, h }));
  }
  return out;
}

export interface Geometry {
  rects: Record<string, Rect>;
  /** Ключи, полностью ушедшие за край ленты: плитка остаётся в DOM (видео не пересоздаётся), но скрыта — адаптивный поток её не грузит. */
  hidden: string[];
  strip: (Rect & { vertical: boolean; max: number }) | null;
}

/**
 * Раскладка: все плитки — в одном контейнере с абсолютными координатами. Переход между режимами меняет только координаты, а не родителя элемента:
 * видео и окно доски не пересоздаются и не переподписываются. Лента прокручивается смещением `offset` (px), без отдельного прокручиваемого контейнера.
 */
export function layoutStage(o: { main: string[]; rest: string[]; mode: "grid" | "stage" | "side"; w: number; h: number; offset?: number; gap?: number; mobile?: boolean; dense?: boolean }): Geometry {
  const gap = o.gap ?? (o.mobile ? 6 : 10);
  const rects: Record<string, Rect> = {};
  const hidden: string[] = [];
  const { w, h } = o;
  if (w <= 0 || h <= 0) return { rects, hidden, strip: null };
  let main = o.main, rest = o.rest, mode = o.mode;
  if (mode === "grid") {
    const inGrid = rest.slice(0, GRID_MAX);
    if (rest.length <= GRID_MAX) { gridRects(inGrid.length, { x: 0, y: 0, w, h }, gap).forEach((r, i) => { rects[inGrid[i]] = r; }); return { rects, hidden, strip: null }; }
    main = inGrid; rest = rest.slice(GRID_MAX); mode = "stage";
  }
  if (o.mobile) mode = "stage";
  const vertical = mode === "side" && w >= 760;
  const stripSize = !rest.length ? 0 : vertical ? clamp(Math.round(w * 0.2), 170, 260) : o.dense ? clamp(Math.round(h * 0.1), 52, 68) : o.mobile ? clamp(Math.round(h * 0.16), 64, 96) : clamp(Math.round(h * 0.17), 92, 150);
  const mainBox: Rect = vertical ? { x: 0, y: 0, w: w - (stripSize ? stripSize + gap : 0), h } : { x: 0, y: 0, w, h: h - (stripSize ? stripSize + gap : 0) };
  gridRects(main.length, mainBox, gap).forEach((r, i) => { rects[main[i]] = r; });
  if (!rest.length) return { rects, hidden, strip: null };
  const tile = vertical ? { w: stripSize, h: Math.round(stripSize * 9 / 16) } : { w: Math.round(stripSize * 16 / 9), h: stripSize };
  const step = (vertical ? tile.h : tile.w) + gap;
  const total = rest.length * step - gap;
  const span = vertical ? h : w;
  const max = Math.max(0, total - span);
  const off = clamp(o.offset ?? 0, 0, max);
  const start = total < span ? (span - total) / 2 : -off;     // мало плиток — по центру; много — прокрутка
  const strip = vertical ? { x: w - stripSize, y: 0, w: stripSize, h, vertical, max } : { x: 0, y: h - stripSize, w, h: stripSize, vertical, max };
  rest.forEach((k, i) => {
    const at = start + i * step;
    rects[k] = rnd(vertical ? { x: strip.x, y: at, w: tile.w, h: tile.h } : { x: at, y: strip.y, w: tile.w, h: tile.h });
    if (at + (vertical ? tile.h : tile.w) <= 0 || at >= span) hidden.push(k);
  });
  return { rects, hidden, strip };
}

/**
 * Активный говорящий с гистерезисом: крупный план не «дёргается» от коротких реплик. Новый говорящий занимает сцену, если говорит непрерывно
 * не меньше switchMs, а текущий продержался не меньше holdMs. Пока текущий говорит, он остаётся. Своё лицо крупно не ставится (вызывающий не передаёт себя).
 */
export class SpeakerTracker {
  private cur: string | null = null;
  private curSince = 0;
  private cand: string | null = null;
  private candSince = 0;
  constructor(private holdMs = 2500, private switchMs = 1200) {}
  get current(): string | null { return this.cur; }
  /** Когда стоит проверить снова (кандидат ждёт своей очереди), или null. */
  nextCheck(now: number): number | null {
    if (!this.cand) return null;
    return Math.max(this.candSince + this.switchMs, this.curSince + this.holdMs) - now;
  }
  update(speaking: string[], now: number, present?: ReadonlySet<string>): string | null {
    if (this.cur && present && !present.has(this.cur)) { this.cur = null; }
    if (!this.cur) {
      if (speaking.length) { this.cur = speaking[0]; this.curSince = now; }
      this.cand = null;
      return this.cur;
    }
    if (speaking.includes(this.cur)) { this.cand = null; return this.cur; }
    const next = speaking[0] ?? null;
    if (!next) { this.cand = null; return this.cur; }
    if (next !== this.cand) { this.cand = next; this.candSince = now; }
    if (now - this.candSince >= this.switchMs && now - this.curSince >= this.holdMs) { this.cur = next; this.curSince = now; this.cand = null; }
    return this.cur;
  }
}

/** Личное состояние сцены зрителя — только для этой встречи (ключ содержит её идентификатор), чтобы закрепления не переезжали в чужие встречи. */
export interface Personal { pins: string[]; layout: LayoutMode }
export const PERSONAL_DEFAULT: Personal = { pins: [], layout: "auto" };
export const storageKey = (meetingId: string) => `pg:stage:${meetingId}`;
export function parsePersonal(raw: string | null): Personal {
  if (!raw) return { ...PERSONAL_DEFAULT };
  try {
    const v = JSON.parse(raw) as Partial<Personal>;
    const pins = Array.isArray(v.pins) ? v.pins.filter((k): k is string => typeof k === "string" && !!parseKey(k)).slice(0, MAX_PINS) : [];
    return { pins: [...new Set(pins)], layout: isLayout(v.layout) ? v.layout : "auto" };
  } catch { return { ...PERSONAL_DEFAULT }; }
}

/** Закрепить/открепить для себя; новое закрепление — в конец, сверх предела вытесняет самое старое. */
export function togglePin(pins: string[], key: string): string[] {
  if (pins.includes(key)) return pins.filter((k) => k !== key);
  return [...pins, key].slice(-MAX_PINS);
}

const clamp = (n: number, a: number, b: number) => Math.min(b, Math.max(a, n));
const rnd = (r: Rect): Rect => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) });
