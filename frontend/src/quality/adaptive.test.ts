import { describe, expect, it } from "vitest";
import { AdaptiveController, applyLevelToEncodings, judge, LEVEL_SPEC } from "./adaptive";
import { lossPercent } from "./manager";

const good = { quality: "good" as const };
const poor = { quality: "poor" as const };

describe("оценка замера", () => {
  it("плохо: низкое качество, ограничение по каналу или процессору, потери, задержка; потеря связи — отдельно", () => {
    expect(judge(poor)).toBe("bad");
    expect(judge({ quality: "good", limited: "bandwidth" })).toBe("bad");
    expect(judge({ quality: "excellent", limited: "cpu" })).toBe("bad");
    expect(judge({ quality: "good", lossPct: 9 })).toBe("bad");
    expect(judge({ quality: "good", rttMs: 700 })).toBe("bad");
    expect(judge({ quality: "lost" })).toBe("lost");
    expect(judge({ quality: "excellent", limited: "none", lossPct: 0.2, rttMs: 40 })).toBe("good");
    expect(judge({ quality: "good", lossPct: 4 })).toBe("neutral");
    expect(judge({ quality: "unknown" })).toBe("neutral");
  });
});

describe("контроллер уровней (звук важнее видео)", () => {
  it("вниз — после двух плохих замеров подряд, по одному шагу до «только звук»", () => {
    const c = new AdaptiveController("high");
    expect(c.push(poor, 0).changed).toBe(false);
    expect(c.push(poor, 5000)).toMatchObject({ level: "medium", changed: true, reason: "degraded" });
    c.push(poor, 10000);
    expect(c.push(poor, 15000).level).toBe("low");
    c.push(poor, 20000); c.push(poor, 25000);
    c.push(poor, 30000); expect(c.push(poor, 35000).level).toBe("off");
    c.push(poor, 40000); expect(c.push(poor, 45000)).toMatchObject({ level: "off", changed: false });      // ниже некуда
  });

  it("одиночный плохой замер не снижает, хороший между плохими сбрасывает счёт", () => {
    const c = new AdaptiveController("high");
    c.push(poor, 0); c.push(good, 5000);
    expect(c.push(poor, 10000).changed).toBe(false);
  });

  it("потеря связи снижает сразу на два уровня", () => {
    const c = new AdaptiveController("high");
    expect(c.push({ quality: "lost" }, 0)).toMatchObject({ level: "low", changed: true });
  });

  it("вверх — только после шести хороших подряд и паузы после ухудшения, по одному шагу", () => {
    const c = new AdaptiveController("high");
    c.push(poor, 0); c.push(poor, 5000);                        // → medium, пауза 20 c
    for (let i = 0; i < 5; i++) expect(c.push(good, 6000 + i * 1000).changed).toBe(false);   // шесть хороших ещё не набралось / пауза
    expect(c.push(good, 12000).changed).toBe(false);            // шесть набрано, но прошло только 7 c от ухудшения
    for (let i = 0; i < 5; i++) c.push(good, 13000 + i * 1000);
    expect(c.push(good, 30000)).toMatchObject({ level: "high", changed: true, reason: "recovered" });
  });

  it("нейтральные замеры ничего не копят", () => {
    const c = new AdaptiveController("high");
    c.push(poor, 0); c.push({ quality: "unknown" }, 1000);
    expect(c.push(poor, 2000).changed).toBe(false);
  });

  it("ручной выбор отключает автоматику, возврат к авто продолжает с текущего уровня", () => {
    const c = new AdaptiveController("high");
    expect(c.setManual("low")).toMatchObject({ level: "low", changed: true, reason: "manual" });
    for (let i = 0; i < 10; i++) expect(c.push(poor, i * 5000).changed).toBe(false);
    expect(c.isManual).toBe(true);
    c.setManual(null);
    expect(c.isManual).toBe(false);
    expect(c.level).toBe("low");
  });
});

describe("параметры кодировок", () => {
  it("верхний слой получает параметры уровня, нижние ограничиваются; звук приоритетнее видео", () => {
    const enc = [{ rid: "q", scaleResolutionDownBy: 4, maxBitrate: 200_000 }, { rid: "h", scaleResolutionDownBy: 2, maxBitrate: 500_000 }, { rid: "f", scaleResolutionDownBy: 1, maxBitrate: 1_500_000 }];
    const out = applyLevelToEncodings(enc, "low");
    const top = out[2];
    expect(top).toMatchObject({ maxBitrate: LEVEL_SPEC.low.maxBitrate, maxFramerate: 10, scaleResolutionDownBy: 4, priority: "low" });
    expect(out[1].maxBitrate).toBeLessThanOrEqual(LEVEL_SPEC.low.maxBitrate / 2);
    expect(out[0].maxBitrate).toBeLessThanOrEqual(out[1].maxBitrate!);
    expect(enc[2].maxBitrate).toBe(1_500_000);                  // исходный массив не изменён
  });
  it("одна кодировка без simulcast тоже получает параметры уровня", () => {
    expect(applyLevelToEncodings([{}], "medium")[0]).toMatchObject({ maxBitrate: 600_000, maxFramerate: 15, scaleResolutionDownBy: 2 });
  });
});

describe("потери между замерами", () => {
  it("считаются по приращению счётчиков", () => {
    expect(lossPercent(undefined, { sent: 100, lost: 1 })).toBeUndefined();
    expect(lossPercent({ sent: 100, lost: 0 }, { sent: 190, lost: 10 })).toBeCloseTo(10, 5);
    expect(lossPercent({ sent: 100, lost: 5 }, { sent: 100, lost: 5 })).toBeUndefined();
    expect(lossPercent({ sent: 100, lost: 5 }, { sent: 50, lost: 5 })).toBeUndefined();     // счётчики сбросились (перезапуск трека)
  });
});
