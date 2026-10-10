import { describe, expect, it } from "vitest";
import { GRID_MAX, MAX_PINS, SpeakerTracker, buildItems, chooseMain, gridRects, keyOf, layoutStage, parseKey, parsePersonal, stripOrder, togglePin, trackSince, type Source } from "./stageModel";

const P = (identity: string, o: Partial<Source> = {}): Source => ({ identity, name: identity.toUpperCase(), local: false, screen: false, ...o });
const items = (ps: Source[], board = false, since = new Map<string, number>()) => buildItems(ps, board, since);

describe("элементы сцены", () => {
  it("каждый показ экрана — отдельный элемент; камера есть у каждого участника", () => {
    const it = items([P("a", { local: true, screen: true }), P("b", { screen: true }), P("c")]);
    expect(it.map((i) => i.key)).toEqual(["screen:a", "screen:b", "camera:a", "camera:b", "camera:c"]);
  });
  it("ключи устойчивы и разбираются обратно; мусор отбрасывается", () => {
    expect(parseKey(keyOf("screen", "u:42"))).toEqual({ type: "screen", identity: "u:42" });
    expect(parseKey("board")).toEqual({ type: "board" });
    expect(parseKey("camera:")).toBeNull();
    expect(parseKey("track:TR_abc")).toBeNull();
  });
  it("момент появления: старые ключи сохраняют время, исчезнувшие забываются", () => {
    const a = trackSince(new Map(), ["screen:a"], 100);
    const b = trackSince(a, ["screen:a", "screen:b"], 200);
    expect([b.get("screen:a"), b.get("screen:b")]).toEqual([100, 200]);
    expect(trackSince(b, ["screen:b"], 300).has("screen:a")).toBe(false);
  });
});

describe("что крупно", () => {
  const ps = [P("me", { local: true }), P("b", { screen: true }), P("c", { screen: true }), P("d")];
  const since = new Map([["screen:b", 10], ["screen:c", 20]]);
  const all = items(ps, false, since);
  it("без закреплений — самый свежий показ экрана", () => {
    expect(chooseMain({ items: all, pins: [], spotlight: [], speaker: null, layout: "auto" })).toMatchObject({ main: ["screen:c"], reason: "screen", mode: "stage" });
  });
  it("личное закрепление важнее общей сцены ведущего", () => {
    const c = chooseMain({ items: all, pins: ["screen:b"], spotlight: ["screen:c"], speaker: null, layout: "auto" });
    expect(c).toMatchObject({ main: ["screen:b"], reason: "pins" });
  });
  it("общая сцена ведущего — когда зритель ничего не закрепил", () => {
    expect(chooseMain({ items: all, pins: [], spotlight: ["camera:d", "screen:b"], speaker: null, layout: "auto" })).toMatchObject({ main: ["camera:d", "screen:b"], reason: "spotlight" });
  });
  it("закрепление отсутствующего элемента не действует (участник ушёл), но и не ломает сцену", () => {
    expect(chooseMain({ items: all, pins: ["screen:zz"], spotlight: [], speaker: null, layout: "auto" }).reason).toBe("screen");
  });
  it("свой экран крупно не ставится, пока в комнате есть другие", () => {
    const own = items([P("me", { local: true, screen: true }), P("b")]);
    expect(chooseMain({ items: own, pins: [], spotlight: [], speaker: null, layout: "auto" }).reason).toBe("grid");
    const alone = items([P("me", { local: true, screen: true })]);
    expect(chooseMain({ items: alone, pins: [], spotlight: [], speaker: null, layout: "auto" }).main).toEqual(["screen:me"]);
  });
  it("«Сетка» — все одинаково, даже при показе экрана; на телефоне сетки нет — крупно один", () => {
    expect(chooseMain({ items: all, pins: ["screen:b"], spotlight: [], speaker: null, layout: "grid" }).mode).toBe("grid");
    expect(chooseMain({ items: all, pins: ["screen:b", "camera:d"], spotlight: [], speaker: null, layout: "grid", mobile: true }).main).toEqual(["screen:b"]);
  });
  it("без показа экрана: «Авто» — сетка, «Сцена» — говорящий, «Рядом» — колонкой", () => {
    const cams = items([P("me", { local: true }), P("b"), P("c")]);
    expect(chooseMain({ items: cams, pins: [], spotlight: [], speaker: "c", layout: "auto" }).mode).toBe("grid");
    expect(chooseMain({ items: cams, pins: [], spotlight: [], speaker: "c", layout: "stage" })).toMatchObject({ main: ["camera:c"], reason: "speaker", mode: "stage" });
    expect(chooseMain({ items: cams, pins: [], spotlight: [], speaker: null, layout: "side" })).toMatchObject({ main: ["camera:b"], mode: "side" });
  });
  it("доска — крупно, если нет показа экрана", () => {
    const b = items([P("me", { local: true }), P("b")], true);
    expect(chooseMain({ items: b, pins: [], spotlight: [], speaker: null, layout: "auto" }).main).toEqual(["board"]);
  });
  it("лента: показы экрана и доска — первыми", () => {
    const b = items([P("me", { local: true }), P("b", { screen: true })], true);
    expect(stripOrder(b, ["camera:b"])).toEqual(["screen:b", "board", "camera:me"]);
  });
});

describe("геометрия", () => {
  it("сетка помещается в прямоугольник и не перекрывается", () => {
    for (const n of [1, 2, 3, 5, 9, 16]) {
      const rs = gridRects(n, { x: 0, y: 0, w: 1280, h: 720 }, 10);
      expect(rs).toHaveLength(n);
      for (const r of rs) { expect(r.x).toBeGreaterThanOrEqual(0); expect(r.y).toBeGreaterThanOrEqual(0); expect(r.x + r.w).toBeLessThanOrEqual(1281); expect(r.y + r.h).toBeLessThanOrEqual(721); }
      for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) {
        const a = rs[i], b = rs[j];
        expect(a.x + a.w <= b.x + 1 || b.x + b.w <= a.x + 1 || a.y + a.h <= b.y + 1 || b.y + b.h <= a.y + 1).toBe(true);
      }
    }
  });
  it("сцена: главное сверху, лента снизу; каждому ключу — координаты", () => {
    const g = layoutStage({ main: ["screen:b"], rest: ["camera:a", "camera:b"], mode: "stage", w: 1200, h: 700 });
    expect(Object.keys(g.rects).sort()).toEqual(["camera:a", "camera:b", "screen:b"]);
    expect(g.rects["screen:b"].y + g.rects["screen:b"].h).toBeLessThanOrEqual(g.strip!.y);
    expect(g.strip!.vertical).toBe(false);
  });
  it("«Рядом»: лента колонкой справа (на узком экране — снизу)", () => {
    const g = layoutStage({ main: ["screen:b"], rest: ["camera:a"], mode: "side", w: 1200, h: 700 });
    expect(g.strip!.vertical).toBe(true);
    expect(g.rects["camera:a"].x).toBeGreaterThan(g.rects["screen:b"].x + g.rects["screen:b"].w);
    expect(layoutStage({ main: ["screen:b"], rest: ["camera:a"], mode: "side", w: 700, h: 700 }).strip!.vertical).toBe(false);
  });
  it("много участников: лента прокручивается, ушедшие за край плитки скрыты, но имеют координаты (не пересоздаются)", () => {
    const rest = Array.from({ length: 30 }, (_, i) => `camera:u${i}`);
    const g0 = layoutStage({ main: ["screen:x"], rest, mode: "stage", w: 1200, h: 700 });
    expect(g0.strip!.max).toBeGreaterThan(0);
    expect(g0.hidden).toContain("camera:u29");
    expect(g0.hidden).not.toContain("camera:u0");
    const g1 = layoutStage({ main: ["screen:x"], rest, mode: "stage", w: 1200, h: 700, offset: 1e9 });
    expect(g1.hidden).toContain("camera:u0");
    expect(g1.hidden).not.toContain("camera:u29");
    expect(Object.keys(g1.rects)).toHaveLength(31);
  });
  it("сетка больше предела: остальные — в ленту", () => {
    const rest = Array.from({ length: GRID_MAX + 4 }, (_, i) => `camera:u${i}`);
    const g = layoutStage({ main: [], rest, mode: "grid", w: 1600, h: 900 });
    expect(g.strip).not.toBeNull();
    expect(Object.keys(g.rects)).toHaveLength(GRID_MAX + 4);
  });
  it("нулевой размер контейнера (вкладка скрыта) — пустая раскладка без ошибок", () => {
    expect(layoutStage({ main: ["a"], rest: ["b"], mode: "stage", w: 0, h: 0 }).rects).toEqual({});
  });
});

describe("активный говорящий с гистерезисом", () => {
  it("короткая реплика не переключает крупный план", () => {
    const t = new SpeakerTracker(2500, 1200);
    expect(t.update(["a"], 0)).toBe("a");
    expect(t.update(["b"], 3000)).toBe("a");
    expect(t.update([], 3500)).toBe("a");       // b замолчал раньше 1,2 с
    expect(t.update(["b"], 4000)).toBe("a");
    expect(t.update(["b"], 5300)).toBe("b");    // говорит дольше 1,2 с, a держался дольше 2,5 с
  });
  it("пока текущий говорит — он остаётся; ушедший из комнаты сменяется", () => {
    const t = new SpeakerTracker(2500, 1200);
    t.update(["a"], 0);
    expect(t.update(["a", "b"], 10000)).toBe("a");
    expect(t.update(["b"], 10001, new Set(["b"]))).toBe("b");
  });
  it("подсказывает, когда проверить снова", () => {
    const t = new SpeakerTracker(2500, 1200);
    t.update(["a"], 0);
    t.update(["b"], 100);
    expect(t.nextCheck(100)).toBe(2400);
  });
});

describe("личное состояние", () => {
  it("закрепления: не больше предела, старое вытесняется; повторное — открепляет", () => {
    let pins: string[] = [];
    for (let i = 0; i < MAX_PINS + 2; i++) pins = togglePin(pins, `camera:u${i}`);
    expect(pins).toHaveLength(MAX_PINS);
    expect(pins[0]).toBe("camera:u2");
    expect(togglePin(pins, "camera:u2")).not.toContain("camera:u2");
  });
  it("сохранённое разбирается безопасно: чужие ключи и мусор отбрасываются", () => {
    expect(parsePersonal("{bad")).toEqual({ pins: [], layout: "auto" });
    expect(parsePersonal(JSON.stringify({ pins: ["screen:a", "TR_xyz", 5, "screen:a"], layout: "side" }))).toEqual({ pins: ["screen:a"], layout: "side" });
    expect(parsePersonal(JSON.stringify({ layout: "huge" })).layout).toBe("auto");
  });
});
