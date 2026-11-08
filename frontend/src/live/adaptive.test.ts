import { describe, expect, it } from "vitest";
import { initial, MAX_HOLD_MS, MIN_HOLD_MS, step, type Sample, type State } from "./adaptive";

const good = (o: Partial<Sample> = {}): Sample => ({ buffer: 3, stalls: 0, stallMs: 0, bwKbps: 9000, probeKbps: null, fragRatio: 0.2, dropRatio: 0, hidden: false, saveData: false, slowNet: false, needKbps: 3000, ...o });

/** Подаёт замеры раз в секунду; возвращает момент первой смены режима (с) или null. */
function run(st: State, seconds: number, make: (t: number) => Sample, start = 0): number | null {
  for (let t = start; t < start + seconds; t++) {
    const d = step(st, make(t), t * 1000);
    if (d.changed) return t;
  }
  return null;
}

describe("автоматическое ухудшение и улучшение просмотра", () => {
  it("хорошая связь: остаёмся на видео", () => {
    const st = initial(0);
    expect(run(st, 300, () => good())).toBeNull();
    expect(st.mode).toBe("video");
  });

  it("две остановки за полминуты переводят на звук", () => {
    const st = initial(0);
    const t = run(st, 60, (t) => good({ stalls: t >= 10 ? (t >= 15 ? 2 : 1) : 0, stallMs: t >= 10 ? 900 : 0, buffer: 2 }));
    expect(t).toBe(15);
    expect(st.mode).toBe("audio");
    expect(st.reason).toBe("частые остановки");
  });

  it("одна короткая остановка не считается", () => {
    const st = initial(0);
    expect(run(st, 120, (t) => good({ stalls: t >= 10 ? 1 : 0, stallMs: t >= 10 ? 400 : 0 }))).toBeNull();
  });

  it("долгое зависание в одну остановку тоже", () => {
    const st = initial(0);
    expect(run(st, 30, (t) => good({ stalls: t >= 5 ? 1 : 0, stallMs: t >= 5 ? 4500 : 0 }))).toBe(5);
  });

  it("пустой буфер дольше 4 секунд, а канал в порядке — звук; короткий провал не в счёт", () => {
    const st = initial(0);
    expect(run(st, 20, (t) => good({ buffer: t >= 3 && t < 6 ? 0.2 : 3 }))).toBeNull();
    expect(run(st, 20, () => good({ buffer: 0.2 }), 100)).toBe(104);
    expect(st.reason).toBe("пустой буфер");
  });

  it("оценка канала ниже потребности с запасом 20 % держится 8 секунд", () => {
    const st = initial(0);
    expect(run(st, 30, () => good({ bwKbps: 3700 }))).toBeNull();
    expect(run(st, 30, () => good({ bwKbps: 2500 }), 100)).toBe(108);
    expect(st.reason).toBe("канал не тянет видео");
  });

  it("сегменты грузятся дольше своей длительности — звук", () => {
    expect(run(initial(0), 30, () => good({ fragRatio: 1.4 }))).toBe(6);
  });

  it("устройство пропускает больше четверти кадров — звук", () => {
    const st = initial(0);
    expect(run(st, 30, () => good({ dropRatio: 0.4 }))).toBe(10);
    expect(st.reason).toBe("устройство не успевает показывать видео");
  });

  it("экономия трафика и медленная сеть — сразу, без ожидания", () => {
    expect(run(initial(0), 3, () => good({ saveData: true }))).toBe(0);
    expect(run(initial(0), 3, () => good({ slowNet: true }))).toBe(0);
  });

  it("страница в фоне 10 секунд — звук; на переднем плане возвращается после проверки скорости", () => {
    const st = initial(0);
    expect(run(st, 30, () => good({ hidden: true }))).toBe(10);
    expect(run(st, 40, () => good({ hidden: true, probeKbps: 20000 }), 31)).toBeNull();
    const back = run(st, 60, () => good({ probeKbps: 20000, buffer: 3 }), 71);
    expect(back).not.toBeNull();
    expect(st.mode).toBe("video");
  });

  it("возврат к видео: только после паузы, подтверждённой скорости (вдвое выше нужной) и 10 спокойных секунд", () => {
    const st = initial(0);
    run(st, 40, (t) => good({ stalls: t >= 10 ? 2 : 0, stallMs: 1000 }));
    expect(st.mode).toBe("audio");
    const at = st.since / 1000;
    // скорость не измерена или низкая — остаёмся на звуке
    expect(run(st, 90, () => good({ probeKbps: null, buffer: 3 }), at + 1)).toBeNull();
    expect(run(st, 90, () => good({ probeKbps: 4000, buffer: 3 }), at + 100)).toBeNull();
    // достаточная скорость: возврат не раньше конца паузы + 10 с
    const t = run(st, 60, () => good({ probeKbps: 9000, buffer: 3 }), at + 200);
    expect(t).toBe(at + 210);
    expect(st.mode).toBe("video");
  });

  it("не возвращается до конца паузы даже при отличной связи", () => {
    const st = initial(0);
    run(st, 20, (t) => good({ stalls: t >= 5 ? 2 : 0, stallMs: 1000 }));
    const at = st.since / 1000;
    expect(run(st, MIN_HOLD_MS / 1000 - 1, () => good({ probeKbps: 50000 }), at + 1)).toBeNull();
  });

  it("вернулись и сразу сорвались: пауза перед следующей попыткой растёт вдвое и не больше предела", () => {
    const st = initial(0);
    let t = 0;
    const holds: number[] = [];
    for (let i = 0; i < 8; i++) {
      // ухудшение
      const t1 = run(st, 400, (x) => good({ stalls: 100 + i * 10 + Math.floor((x - t) / 2), stallMs: 10_000 + (x - t) * 500 }), t);
      expect(st.mode).toBe("audio");
      holds.push(st.holdUntil - st.since);
      t = (t1 ?? t) + 1;
      // возврат после паузы
      const t2 = run(st, 700, () => good({ probeKbps: 90000, buffer: 4 }), t);
      expect(t2).not.toBeNull();
      t = (t2 ?? t) + 1;
    }
    expect(holds[0]).toBe(MIN_HOLD_MS);
    expect(holds[1]).toBeGreaterThan(holds[0]);
    expect(Math.max(...holds)).toBeLessThanOrEqual(MAX_HOLD_MS);
  });

  it("минута хорошего видео после возврата прощает прежние неудачи", () => {
    const st = initial(0);
    st.failedUpgrades = 3; st.lastUpgradeAt = 1000;
    run(st, 90, () => good(), 2);
    expect(st.failedUpgrades).toBe(0);
  });
});
