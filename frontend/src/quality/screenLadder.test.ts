import { describe, expect, it } from "vitest";
import type { Sample } from "./adaptive";
import { ladderParams, ladderNote, MAX_LEVEL, ScreenLadder } from "./screenLadder";

const good: Sample = { quality: "excellent", limited: "none", lossPct: 0.2, rttMs: 40 };
const bad: Sample = { quality: "poor", limited: "bandwidth", lossPct: 19, rttMs: 220 };

/** Подаёт замеры раз в 5 с; возвращает список (время, ступень) при каждой смене. */
function run(l: ScreenLadder, n: number, s: Sample, from = 0): { t: number; level: number }[] {
  const out: { t: number; level: number }[] = [];
  for (let i = 0; i < n; i++) { const t = (from + i) * 5000; const d = l.push(s, t); if (d.changed) out.push({ t: from + i, level: d.level }); }
  return out;
}

describe("автоподстройка показа экрана у выступающего", () => {
  it("хороший канал: ступень 0, ничего не меняется", () => {
    expect(run(new ScreenLadder(), 200, good)).toEqual([]);
  });

  it("канал не тянет (браузер сообщает bandwidth, потери 19 %): после двух плохих замеров ступень вниз, дальше по одной", () => {
    const l = new ScreenLadder();
    expect(run(l, 12, bad).map((x) => x.level)).toEqual([1, 2, 3]);
    expect(l.level).toBe(MAX_LEVEL);
  });

  it("потеря связи — сразу на две ступени", () => {
    const l = new ScreenLadder();
    const d = l.push({ quality: "lost" }, 0);
    expect(d.changed).toBe(true); expect(l.level).toBe(2);
  });

  it("после стабильной связи возвращается по ступеням, не раньше пауз", () => {
    const l = new ScreenLadder();
    run(l, 12, bad);                                           // дошли до дна
    const up = run(l, 60, good, 12);
    expect(up.map((x) => x.level)).toEqual([2, 1, 0]);
    expect(up[0].t - 12 + 1).toBeGreaterThanOrEqual(6);        // не быстрее шести хороших замеров подряд
  });

  it("нейтральные замеры не копят ни плохое, ни хорошее", () => {
    const l = new ScreenLadder();
    const neutral: Sample = { quality: "good", lossPct: 4, rttMs: 400 };
    expect(run(l, 100, neutral)).toEqual([]);
  });

  it("если после подъёма снова плохо, следующий подъём ждёт вдвое дольше", () => {
    const l = new ScreenLadder();
    run(l, 3, bad);                                            // ступень 1
    const a = run(l, 8, good, 3);                              // вернулись на 0 через шесть хороших замеров
    expect(a.at(-1)?.level).toBe(0);
    run(l, 3, bad, 11);                                        // сразу сорвались (меньше полутора минут после подъёма)
    expect(l.level).toBeGreaterThan(0);
    const b = run(l, 80, good, 14);
    expect(b[0].t - 14 + 1).toBeGreaterThanOrEqual(12);        // теперь не меньше двенадцати хороших подряд
  });

  it("параметры отправителя считаются от профиля: чем ниже ступень, тем меньше битрейт, частота и разрешение", () => {
    const base = { maxBitrate: 4_000_000, maxFramerate: 15, scale: 1 };
    expect(ladderParams(0, base)).toEqual({ maxBitrate: 4_000_000, maxFramerate: 15, scaleResolutionDownBy: 1 });
    const p3 = ladderParams(3, base);
    expect(p3.maxBitrate).toBe(880_000); expect(p3.maxFramerate).toBe(6); expect(p3.scaleResolutionDownBy).toBe(3);
    expect(ladderParams(3, { maxBitrate: 400_000, maxFramerate: 5, scale: 1.5 })).toEqual({ maxBitrate: 150_000, maxFramerate: 4, scaleResolutionDownBy: 4.5 });
    expect(ladderParams(2, { scale: 1 }).maxBitrate).toBe(1_000_000);
  });

  it("сообщения спокойные: что случилось, чем не грозит, что вернётся само", () => {
    expect(ladderNote("degraded", 1, "1920×1080")).toMatch(/Звук и зрители не страдают.*Вернём качество само/);
    expect(ladderNote("recovered", 0, "")).toBe("Связь стабильна: показ экрана снова в полном качестве.");
  });
});
