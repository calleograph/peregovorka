import { describe, expect, it } from "vitest";
import { signalOf } from "./signal";

describe("индикатор сигнала", () => {
  it("по уровню видео: зелёный / жёлтый / красный", () => {
    expect(signalOf("high", "excellent")).toMatchObject({ bars: 4, tone: "ok" });
    expect(signalOf("high", "poor")).toMatchObject({ tone: "ok" });             // одиночный плохой замер уровень не меняет — значок не мигает
    expect(signalOf("medium", "good")).toMatchObject({ bars: 3, tone: "warn" });
    expect(signalOf("low", "good")).toMatchObject({ bars: 2, tone: "warn" });
    expect(signalOf("min", "poor")).toMatchObject({ bars: 1, tone: "bad" });
    expect(signalOf("off", "poor")).toMatchObject({ tone: "bad" });
  });
  it("потеря связи — красный сразу", () => {
    expect(signalOf("high", "lost")).toMatchObject({ bars: 1, tone: "bad" });
  });
});
