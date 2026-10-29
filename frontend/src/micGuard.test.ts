import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MicGuard, SILENT_AFTER_MS, type MicGuardState } from "./micGuard";

const setup = (reopen: () => Promise<void>, wanted = () => true) => {
  const states: MicGuardState[] = [];
  const g = new MicGuard({ reopen, wanted, onState: (s) => states.push(s), delays: [100, 200, 400] });
  return { g, states, phases: () => states.map((s) => s.phase) };
};

describe("восстановление микрофона", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("после потери повторяет попытки и сообщает о восстановлении", async () => {
    let n = 0;
    const { g, phases, states } = setup(async () => { if (++n < 3) throw new Error("busy"); });
    g.lost("ended");
    await vi.advanceTimersByTimeAsync(100); await vi.advanceTimersByTimeAsync(200);
    expect(n).toBe(2);
    await vi.advanceTimersByTimeAsync(400);
    expect(n).toBe(3);
    expect(phases()).toEqual(["lost", "lost", "lost", "recovered"]);
    expect(states.at(-1)).toEqual({ phase: "recovered", attempts: 3 });
    expect(g.active).toBe(false);
  });

  it("после последней неудачной попытки сообщает об ошибке и останавливается", async () => {
    const err = new Error("NotReadableError");
    const { g, states } = setup(async () => { throw err; });
    g.lost("ended");
    await vi.advanceTimersByTimeAsync(5000);
    expect(states.at(-1)).toEqual({ phase: "failed", error: err, attempts: 3 });
    expect(g.active).toBe(false);
  });

  it("не пытается, если пользователь выключил микрофон сам", async () => {
    const reopen = vi.fn(async () => undefined);
    let wanted = true;
    const { g } = setup(reopen, () => wanted);
    g.lost("ended");
    wanted = false;
    await vi.advanceTimersByTimeAsync(1000);
    expect(reopen).not.toHaveBeenCalled();
    expect(g.active).toBe(false);
  });

  it("новое устройство — попытка сразу, повторная потеря не дублирует цикл", async () => {
    let n = 0;
    const { g, phases } = setup(async () => { if (++n < 2) throw new Error("x"); });
    g.lost("ended"); g.lost("ended");
    g.retryNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(n).toBe(1);
    g.retryNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(n).toBe(2);
    expect(phases().at(-1)).toBe("recovered");
  });

  it("тишину от системы сообщает только после порога и снимает, когда звук вернулся", async () => {
    const { g, phases } = setup(async () => undefined);
    g.silent(true);
    await vi.advanceTimersByTimeAsync(SILENT_AFTER_MS - 1);
    expect(phases()).toEqual([]);
    await vi.advanceTimersByTimeAsync(2);
    expect(phases()).toEqual(["silent"]);
    g.silent(false);
    expect(phases()).toEqual(["silent", "ok"]);
    g.silent(true); g.silent(false);                                  // короткая пауза при переключении — без сообщений
    await vi.advanceTimersByTimeAsync(SILENT_AFTER_MS * 2);
    expect(phases()).toEqual(["silent", "ok", "ok"]);
  });
});
