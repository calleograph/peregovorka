import { describe, expect, it } from "vitest";
// @ts-expect-error — обычный JS без типов: этот же файл загружается в браузере как AudioWorklet
import { DEFAULTS, GateCore } from "../../public/audio/noise-gate.worklet.js";

const SR = 48000;
const rms = (a: number[]) => Math.sqrt(a.reduce((s, v) => s + v * v, 0) / Math.max(1, a.length));
const db = (x: number) => 20 * Math.log10(x + 1e-12);
const tone = (n: number, amp: number, f = 220) => Array.from({ length: n }, (_, i) => amp * Math.sin((2 * Math.PI * f * i) / SR));
const noise = (n: number, amp: number) => { let s = 12345; return Array.from({ length: n }, () => { s = (s * 1664525 + 1013904223) >>> 0; return amp * ((s / 2 ** 32) * 2 - 1); }); };
const run = (g: InstanceType<typeof GateCore>, x: number[]) => x.map((v) => g.step(v));

describe("шумовой затвор после RNNoise", () => {
  it("речь проходит без обрезания: усиление — только компенсация RNNoise; тихие слоги не гасятся", () => {
    const g = new GateCore(SR, {});
    run(g, tone(SR, 0.2));                                                  // секунда «громкой речи» задаёт опорный уровень
    const loud = run(g, tone(SR / 2, 0.2)); const quiet = run(g, tone(SR / 2, 0.2 * 10 ** (-20 / 20)));   // слог на 20 дБ тише громкого
    expect(db(rms(loud.slice(2000))) - db(rms(tone(SR / 2, 0.2).slice(2000)))).toBeCloseTo(DEFAULTS.makeupDb, 0);
    expect(db(rms(quiet.slice(9000))) - db(rms(tone(SR / 2, 0.02).slice(9000)))).toBeGreaterThan(DEFAULTS.makeupDb - 1);
  });
  it("остаток шума в паузе после речи ослабляется на глубину затвора, но не обнуляется", () => {
    const g = new GateCore(SR, {});
    run(g, tone(SR, 0.2));
    run(g, new Array(Math.round(SR * 0.5)).fill(0));                         // выдержка закончилась
    const resid = noise(SR, 0.004);                                          // −48 дБ: ниже порога (−24 дБ от речи ≈ −37 дБ)
    const out = run(g, resid);
    const att = db(rms(resid.slice(SR / 2))) - db(rms(out.slice(SR / 2))) + DEFAULTS.makeupDb;
    expect(att).toBeGreaterThan(DEFAULTS.reduceDb - 2);
    expect(rms(out)).toBeGreaterThan(0);
  });
  it("начало слова после паузы не срезается: затвор открывается с упреждением", () => {
    const g = new GateCore(SR, {});
    run(g, tone(SR, 0.2)); run(g, new Array(SR).fill(0));
    const onset = run(g, tone(Math.round(SR * 0.05), 0.2));
        expect(db(rms(onset.slice(Math.round(SR * 0.03), Math.round(SR * 0.05))))).toBeGreaterThan(db(0.2 / Math.SQRT2) - 3);   // к 30 мс после начала — полный уровень (затвор открылся с упреждением)
  });
  it("короткий щелчок (мышь, клавиша) затвор не открывает, а слитная речь — открывает", () => {
    const g = new GateCore(SR, {});
    run(g, tone(SR, 0.2)); run(g, new Array(SR).fill(0));                    // речь была, затвор закрылся
    const click = run(g, [...tone(Math.round(SR * 0.006), 0.2, 2000), ...new Array(SR / 4).fill(0)]);
    expect(Math.max(...click.map(Math.abs))).toBeLessThan(0.2 * 0.35 * 10 ** (DEFAULTS.makeupDb / 20));          // щелчок ослаблен минимум на ≈9 дБ относительно «открытого» прохода
    run(g, new Array(SR).fill(0));
    const word = run(g, tone(Math.round(SR * 0.2), 0.2));
    expect(db(rms(word.slice(Math.round(SR * 0.05))))).toBeGreaterThan(db(0.2 / Math.SQRT2) - 3);
  });
  it("короткая пауза внутри фразы (меньше выдержки) не ослабляется", () => {
    const g = new GateCore(SR, {});
    run(g, tone(SR, 0.2));
    const gap = run(g, noise(Math.round(SR * 0.12), 0.003));
    expect(g.g).toBeGreaterThan(0.95);
    expect(gap.length).toBeGreaterThan(0);
  });
  it("мягкий ограничитель: выход не превышает 1", () => {
    const g = new GateCore(SR, {});
    const out = run(g, tone(SR, 0.95));
    expect(Math.max(...out.map(Math.abs))).toBeLessThanOrEqual(1);
  });
});
