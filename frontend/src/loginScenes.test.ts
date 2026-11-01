import { describe, expect, it } from "vitest";
import { FrameGovernor, sceneMode } from "./loginScenes";

describe("sceneMode", () => {
  it("компьютер — живой фон; телефон и планшет — упрощённый статичный", () => {
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
  it("плавно — полное движение; тяжело — спокойнее; очень тяжело — стоп", () => {
    expect(feed(new FrameGovernor(), 16.7)).toBe(0);
    expect(feed(new FrameGovernor(), 40)).toBe(1);
    expect(feed(new FrameGovernor(), 80)).toBe(2);
  });
  it("уровень не возвращается назад и не меняется по короткому окну", () => {
    const g = new FrameGovernor();
    expect(feed(g, 40)).toBe(1);
    expect(feed(g, 16)).toBe(1);
    expect(feed(g, 90, 5)).toBe(1);
  });
});
