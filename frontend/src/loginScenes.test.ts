import { describe, expect, it } from "vitest";
import { FrameGovernor, sceneMode, wavePath } from "./loginScenes";

describe("sceneMode", () => {
  it("компьютер — живой фон; телефон и планшет — упрощённый неподвижный", () => {
    expect(sceneMode({ width: 1440, coarse: false, reduced: false, cores: 8 })).toBe("animated");
    expect(sceneMode({ width: 390, coarse: true, reduced: false, cores: 8 })).toBe("mobile");
    expect(sceneMode({ width: 1024, coarse: true, reduced: false, cores: 8 })).toBe("mobile");          // планшет
    expect(sceneMode({ width: 700, coarse: false, reduced: false, cores: 8 })).toBe("mobile");          // узкое окно на компьютере
  });
  it("«уменьшить движение», экономия трафика, слабый процессор — без движения", () => {
    expect(sceneMode({ width: 1440, coarse: false, reduced: true })).toBe("still");
    expect(sceneMode({ width: 1440, coarse: false, reduced: false, saveData: true })).toBe("still");
    expect(sceneMode({ width: 1440, coarse: false, reduced: false, cores: 2 })).toBe("still");
  });
});

describe("FrameGovernor", () => {
  const feed = (g: FrameGovernor, dt: number, n = 40) => { for (let i = 0; i < n; i++) g.push(dt); return g.settle(); };
  it("плавно — полное движение; умеренно медленно — на ступень спокойнее (и снова — стоп); очень медленно — сразу стоп", () => {
    expect(feed(new FrameGovernor(), 16.7)).toBe(0);
    const g = new FrameGovernor();
    expect(feed(g, 40)).toBe(1);
    expect(feed(g, 40)).toBe(2);
    expect(feed(new FrameGovernor(), 80)).toBe(2);
  });
  it("очень медленные кадры (6 в секунду — 12 замеров за окно) тоже замечаются", () => {
    expect(feed(new FrameGovernor(), 166, 12)).toBe(2);
  });
  it("уровень не возвращается назад и не меняется по короткому окну", () => {
    const g = new FrameGovernor();
    expect(feed(g, 40)).toBe(1);
    expect(feed(g, 16)).toBe(1);
    expect(feed(g, 90, 5)).toBe(1);
  });
});

describe("wavePath", () => {
  it("две волны подряд без шва: начало и конец периода на одной высоте, путь покрывает всю ширину", () => {
    const pts = wavePath(1200, 300, [40, 15], 1.3, 2).slice(1).split("L").map((p) => p.split(" ").map(Number));
    expect(pts[0][0]).toBe(0);
    expect(pts[pts.length - 1][0]).toBe(2400);
    expect(pts[64][0]).toBe(1200);
    expect(pts[64][1]).toBeCloseTo(pts[0][1], 0);
    expect(pts[pts.length - 1][1]).toBeCloseTo(pts[0][1], 0);
    expect(Math.max(...pts.map((p) => p[1])) - Math.min(...pts.map((p) => p[1]))).toBeGreaterThan(60);
  });
});
